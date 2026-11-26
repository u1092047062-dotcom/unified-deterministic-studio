use std::{collections::HashMap, path::PathBuf, time::Instant};

use chrono::{DateTime, Local};
use tokio::io::AsyncWriteExt;
use windows_sys::Win32::{
    Foundation::{CloseHandle, FILETIME, GetLastError},
    System::{
        Diagnostics::ToolHelp::{
            CreateToolhelp32Snapshot, PROCESSENTRY32W, Process32FirstW, Process32NextW,
            TH32CS_SNAPPROCESS,
        },
        Threading::{GetProcessTimes, OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION},
    },
};

#[derive(Clone, Debug)]
struct ProcessInfo {
    name: String,
    creation_time: Option<u64>,
}

#[derive(Clone, Debug)]
pub struct ProcessSnapshot {
    pub name: String,
    pub start_instant: Instant,
    pub start_time: DateTime<Local>,
    creation_time: Option<u64>,
}

#[derive(Debug)]
pub enum AuditEvent {
    Started {
        pid: u32,
        name: String,
        time: DateTime<Local>,
        is_priority: bool,
    },
    Stopped {
        pid: u32,
        name: String,
        start_time: DateTime<Local>,
        end_time: DateTime<Local>,
        duration_str: String,
        is_priority: bool,
    },
}

pub struct ProcessMonitor {
    active_processes: HashMap<u32, ProcessSnapshot>,
    log_file_path: PathBuf,
    extra_watchlist: Vec<String>,
    initialized: bool,
}

impl Default for ProcessMonitor {
    fn default() -> Self {
        Self::new()
    }
}

impl ProcessMonitor {
    const DEFAULT_WATCHLIST: &'static [&'static str] = &[
        "cmd.exe",
        "powershell.exe",
        "pwsh.exe",
        "taskmgr.exe",
        "regedit.exe",
        "mstsc.exe",
        "anydesk.exe",
        "teamviewer.exe",
    ];

    pub fn new() -> Self {
        let mut base = std::env::current_exe().unwrap_or_else(|_| PathBuf::from("."));
        base.pop();
        let log_file_path = base.join("process_audit.log");
        let extra_watchlist = Self::load_extra_watchlist_from_env();

        Self {
            active_processes: HashMap::new(),
            log_file_path,
            extra_watchlist,
            initialized: false,
        }
    }

    fn load_extra_watchlist_from_env() -> Vec<String> {
        let eda_raw = std::env::var("EDA_WATCHLIST_PROCESSES").unwrap_or_default();
        let platform_raw = std::env::var("PLATFORM_WATCHLIST_PROCESSES").unwrap_or_default();
        Self::parse_combined_watchlists(&[eda_raw.as_str(), platform_raw.as_str()])
    }

    fn parse_combined_watchlists(sources: &[&str]) -> Vec<String> {
        let mut combined = Vec::new();
        for raw in sources {
            for item in Self::parse_extra_watchlist(raw) {
                if !combined
                    .iter()
                    .any(|existing: &String| existing.eq_ignore_ascii_case(&item))
                {
                    combined.push(item);
                }
            }
        }
        combined
    }

    fn parse_extra_watchlist(raw: &str) -> Vec<String> {
        raw.split(',')
            .map(str::trim)
            .filter(|item| !item.is_empty())
            .map(str::to_string)
            .collect()
    }

    fn is_priority_process_with_extra(name: &str, extra_watchlist: &[String]) -> bool {
        Self::DEFAULT_WATCHLIST
            .iter()
            .any(|item| name.eq_ignore_ascii_case(item))
            || extra_watchlist
                .iter()
                .any(|item| name.eq_ignore_ascii_case(item))
    }

    #[allow(dead_code)]
    fn is_priority_process(name: &str) -> bool {
        let extra = Self::load_extra_watchlist_from_env();
        Self::is_priority_process_with_extra(name, &extra)
    }

    /// Returns a snapshot error instead of silently treating it as an empty process list.
    fn try_get_current_process_table(&self) -> Result<HashMap<u32, ProcessInfo>, String> {
        let mut table = HashMap::new();

        unsafe {
            let snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
            if snapshot.is_null() || snapshot == -1isize as *mut std::ffi::c_void {
                return Err(format!(
                    "CreateToolhelp32Snapshot fehlgeschlagen (Windows-Fehler {})",
                    GetLastError()
                ));
            }

            let mut entry: PROCESSENTRY32W = std::mem::zeroed();
            entry.dwSize = std::mem::size_of::<PROCESSENTRY32W>() as u32;

            if Process32FirstW(snapshot, &mut entry) == 0 {
                let error = GetLastError();
                CloseHandle(snapshot);
                return Err(format!(
                    "Process32FirstW fehlgeschlagen (Windows-Fehler {})",
                    error
                ));
            }

            loop {
                let end = entry
                    .szExeFile
                    .iter()
                    .position(|&ch| ch == 0)
                    .unwrap_or(entry.szExeFile.len());
                let name = String::from_utf16_lossy(&entry.szExeFile[..end]);

                let creation_time = Self::get_process_creation_time(entry.th32ProcessID);
                table.insert(
                    entry.th32ProcessID,
                    ProcessInfo {
                        name,
                        creation_time,
                    },
                );

                if Process32NextW(snapshot, &mut entry) == 0 {
                    let error = GetLastError();
                    CloseHandle(snapshot);
                    // ERROR_NO_MORE_FILES (18) is the normal end of enumeration.
                    if error != 18 && error != 0 {
                        return Err(format!(
                            "Process32NextW fehlgeschlagen (Windows-Fehler {})",
                            error
                        ));
                    }
                    break;
                }
            }
        }

        Ok(table)
    }

    fn get_process_creation_time(pid: u32) -> Option<u64> {
        unsafe {
            let handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid);
            if handle.is_null() {
                return None;
            }

            let mut creation = FILETIME {
                dwLowDateTime: 0,
                dwHighDateTime: 0,
            };
            let mut exit = FILETIME {
                dwLowDateTime: 0,
                dwHighDateTime: 0,
            };
            let mut kernel = FILETIME {
                dwLowDateTime: 0,
                dwHighDateTime: 0,
            };
            let mut user = FILETIME {
                dwLowDateTime: 0,
                dwHighDateTime: 0,
            };

            let ok = GetProcessTimes(handle, &mut creation, &mut exit, &mut kernel, &mut user);
            CloseHandle(handle);

            if ok == 0 {
                None
            } else {
                Some(((creation.dwHighDateTime as u64) << 32) | creation.dwLowDateTime as u64)
            }
        }
    }

    /// Compatibility API used by the Telegram /ps command.
    pub fn get_current_process_table(&self) -> HashMap<u32, String> {
        let table = match self.try_get_current_process_table() {
            Ok(table) => table,
            Err(error) => {
                log::error!(
                    "Process-Monitor: Prozessliste für /ps nicht verfügbar: {}",
                    error
                );
                return HashMap::new();
            }
        };
        table
            .into_iter()
            .map(|(pid, info)| (pid, info.name))
            .collect()
    }

    pub async fn check_system(&mut self) -> Result<Vec<AuditEvent>, String> {
        let current_table = self.try_get_current_process_table()?;
        let now_local = Local::now();
        let now_instant = Instant::now();
        let mut events = Vec::new();

        // Initial observation establishes a baseline. Existing applications are
        // not falsely reported as newly started after service startup/restart.
        if !self.initialized {
            for (&pid, info) in &current_table {
                self.active_processes.insert(
                    pid,
                    ProcessSnapshot {
                        name: info.name.clone(),
                        start_instant: now_instant,
                        start_time: now_local,
                        creation_time: info.creation_time,
                    },
                );
            }
            self.initialized = true;
            log::info!(
                "Process-Monitor: Ausgangsbestand mit {} Prozessen erfasst.",
                current_table.len()
            );
            return Ok(events);
        }

        // A PID is considered the same process only when both its name and
        // Windows creation timestamp match. This protects against PID reuse.
        let replaced_or_terminated: Vec<u32> = self
            .active_processes
            .iter()
            .filter_map(|(&pid, previous)| match current_table.get(&pid) {
                Some(current)
                    if Self::same_process_identity(
                        &previous.name,
                        previous.creation_time,
                        current,
                    ) =>
                {
                    None
                }
                _ => Some(pid),
            })
            .collect();

        for pid in replaced_or_terminated {
            if let Some(previous) = self.active_processes.remove(&pid) {
                let duration = now_instant.saturating_duration_since(previous.start_instant);
                let duration_str = Self::format_duration(duration.as_secs());
                let priority =
                    Self::is_priority_process_with_extra(&previous.name, &self.extra_watchlist);
                let log_msg = format!(
                    "[STOP] PID: {} | Programm: {} | Start: {} | Ende: {} | Nutzungsdauer: {}\n",
                    pid,
                    previous.name,
                    previous.start_time.format("%Y-%m-%d %H:%M:%S"),
                    now_local.format("%Y-%m-%d %H:%M:%S"),
                    duration_str
                );
                if let Err(error) = self.append_to_audit_log(&log_msg).await {
                    log::error!(
                        "Audit-Log: STOP-Ereignis für PID {} konnte nicht geschrieben werden: {}",
                        pid,
                        error
                    );
                }

                events.push(AuditEvent::Stopped {
                    pid,
                    name: previous.name,
                    start_time: previous.start_time,
                    end_time: now_local,
                    duration_str,
                    is_priority: priority,
                });
            }
        }

        for (&pid, info) in &current_table {
            if !self.active_processes.contains_key(&pid) {
                let priority =
                    Self::is_priority_process_with_extra(&info.name, &self.extra_watchlist);
                let log_msg = format!(
                    "[START] PID: {} | Programm: {} | Start: {}\n",
                    pid,
                    info.name,
                    now_local.format("%Y-%m-%d %H:%M:%S")
                );
                if let Err(error) = self.append_to_audit_log(&log_msg).await {
                    log::error!(
                        "Audit-Log: START-Ereignis für PID {} konnte nicht geschrieben werden: {}",
                        pid,
                        error
                    );
                }

                self.active_processes.insert(
                    pid,
                    ProcessSnapshot {
                        name: info.name.clone(),
                        start_instant: now_instant,
                        start_time: now_local,
                        creation_time: info.creation_time,
                    },
                );

                events.push(AuditEvent::Started {
                    pid,
                    name: info.name.clone(),
                    time: now_local,
                    is_priority: priority,
                });
            }
        }

        Ok(events)
    }

    fn same_process_identity(
        previous_name: &str,
        previous_creation_time: Option<u64>,
        current: &ProcessInfo,
    ) -> bool {
        previous_name.eq_ignore_ascii_case(&current.name)
            && previous_creation_time == current.creation_time
    }

    fn format_duration(seconds: u64) -> String {
        let hours = seconds / 3600;
        let minutes = (seconds % 3600) / 60;
        let secs = seconds % 60;
        format!("{}h {}m {}s", hours, minutes, secs)
    }

    async fn append_to_audit_log(
        &self,
        text: &str,
    ) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        if let Some(parent) = self.log_file_path.parent() {
            tokio::fs::create_dir_all(parent).await?;
        }
        let mut file = tokio::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.log_file_path)
            .await?;
        // Rust strings are UTF-8; write_all preserves the encoded bytes.
        file.write_all(text.as_bytes()).await?;
        file.flush().await?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn process(name: &str, creation_time: Option<u64>) -> ProcessInfo {
        ProcessInfo {
            name: name.to_string(),
            creation_time,
        }
    }

    #[test]
    fn duration_formats_zero_correctly() {
        assert_eq!(ProcessMonitor::format_duration(0), "0h 0m 0s");
    }

    #[test]
    fn duration_formats_hours_minutes_and_seconds() {
        assert_eq!(ProcessMonitor::format_duration(3_661), "1h 1m 1s");
    }

    #[test]
    fn same_pid_and_creation_time_means_same_process() {
        let current = process("powershell.exe", Some(123_456));
        assert!(ProcessMonitor::same_process_identity(
            "powershell.exe",
            Some(123_456),
            &current
        ));
    }

    #[test]
    fn changed_creation_time_detects_pid_reuse() {
        let current = process("powershell.exe", Some(222_222));
        assert!(!ProcessMonitor::same_process_identity(
            "powershell.exe",
            Some(111_111),
            &current
        ));
    }

    #[test]
    fn changed_process_name_detects_replacement() {
        let current = process("notepad.exe", Some(123_456));
        assert!(!ProcessMonitor::same_process_identity(
            "powershell.exe",
            Some(123_456),
            &current
        ));
    }

    #[test]
    fn process_name_comparison_is_case_insensitive() {
        let current = process("POWERSHELL.EXE", Some(123_456));
        assert!(ProcessMonitor::same_process_identity(
            "powershell.exe",
            Some(123_456),
            &current
        ));
    }

    #[test]
    fn unavailable_creation_time_uses_name_fallback() {
        // If Windows denies process-time access for both observations, PID reuse
        // cannot be distinguished by creation time alone.
        let current = process("powershell.exe", None);
        assert!(ProcessMonitor::same_process_identity(
            "powershell.exe",
            None,
            &current
        ));
    }

    #[test]
    fn newly_available_creation_time_detects_uncertain_identity() {
        let current = process("powershell.exe", Some(123_456));
        assert!(!ProcessMonitor::same_process_identity(
            "powershell.exe",
            None,
            &current
        ));
    }

    #[test]
    fn lost_creation_time_detects_uncertain_identity() {
        let current = process("powershell.exe", None);
        assert!(!ProcessMonitor::same_process_identity(
            "powershell.exe",
            Some(123_456),
            &current
        ));
    }

    #[test]
    fn unavailable_creation_time_still_checks_process_name() {
        let current = process("notepad.exe", None);
        assert!(!ProcessMonitor::same_process_identity(
            "powershell.exe",
            None,
            &current
        ));
    }

    #[test]
    fn priority_process_matching_is_case_insensitive() {
        assert!(ProcessMonitor::is_priority_process("POWERSHELL.EXE"));
        assert!(!ProcessMonitor::is_priority_process("notepad.exe"));
    }

    #[test]
    fn audit_log_text_is_valid_utf8_and_keeps_german_characters() {
        let entry = "[STOP] Programm: Überwachung – Prüfung äöü ß\n";
        let decoded = std::str::from_utf8(entry.as_bytes()).expect("Audit-Log muss UTF-8 sein");
        assert!(decoded.contains("Überwachung"));
        assert!(decoded.contains("äöü ß"));
    }

    #[test]
    fn parse_extra_watchlist_trims_and_ignores_empty_entries() {
        let parsed = ProcessMonitor::parse_extra_watchlist(
            " eda-cad-studio.exe , , python.exe,kicad-cli.exe,   ",
        );
        assert_eq!(
            parsed,
            vec!["eda-cad-studio.exe", "python.exe", "kicad-cli.exe"]
        );
    }

    #[test]
    fn priority_matching_combines_defaults_and_eda_watchlist_case_insensitively() {
        let extra = ProcessMonitor::parse_extra_watchlist("eda-cad-studio.exe,uvicorn.exe");
        assert!(ProcessMonitor::is_priority_process_with_extra(
            "POWERSHELL.EXE",
            &extra
        ));
        assert!(ProcessMonitor::is_priority_process_with_extra(
            "EDA-CAD-STUDIO.EXE",
            &extra
        ));
        assert!(ProcessMonitor::is_priority_process_with_extra(
            "uvicorn.exe",
            &extra
        ));
        assert!(!ProcessMonitor::is_priority_process_with_extra(
            "notepad.exe",
            &extra
        ));
    }

    #[test]
    fn priority_matching_with_empty_extra_watchlist_keeps_defaults() {
        let extra = ProcessMonitor::parse_extra_watchlist(" , ");
        assert!(extra.is_empty());
        assert!(ProcessMonitor::is_priority_process_with_extra(
            "cmd.exe", &extra
        ));
        assert!(!ProcessMonitor::is_priority_process_with_extra(
            "eda-cad-studio.exe",
            &extra
        ));
    }

    #[test]
    fn parse_combined_watchlists_merges_eda_and_platform_without_duplicates() {
        let combined = ProcessMonitor::parse_combined_watchlists(&[
            "eda-cad-studio.exe, python.exe",
            "PYTHON.EXE, tpai.exe, gigapixel.exe",
        ]);
        assert_eq!(
            combined,
            vec![
                "eda-cad-studio.exe",
                "python.exe",
                "tpai.exe",
                "gigapixel.exe"
            ]
        );
        assert!(ProcessMonitor::is_priority_process_with_extra(
            "TPAI.EXE", &combined
        ));
    }
}
