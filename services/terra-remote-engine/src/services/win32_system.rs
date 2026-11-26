use image::codecs::png::PngEncoder;
use image::{ColorType, ImageEncoder};
use std::io::Cursor;
use std::ptr;
use windows_sys::Win32::Foundation::{CloseHandle, GetLastError, HWND, LRESULT};
use windows_sys::Win32::Graphics::Gdi::{
    BITMAPINFOHEADER, BitBlt, CreateCompatibleBitmap, CreateCompatibleDC, DIB_RGB_COLORS, DeleteDC,
    DeleteObject, GetDC, GetDIBits, ReleaseDC, SRCCOPY, SelectObject,
};
use windows_sys::Win32::Security::{
    AdjustTokenPrivileges, LUID_AND_ATTRIBUTES, LookupPrivilegeValueW, SE_PRIVILEGE_ENABLED,
    TOKEN_ADJUST_PRIVILEGES, TOKEN_PRIVILEGES, TOKEN_QUERY,
};
use windows_sys::Win32::System::Power::{
    GetSystemPowerStatus, SYSTEM_POWER_STATUS, SetSuspendState,
};
use windows_sys::Win32::System::ProcessStatus::{GetProcessMemoryInfo, PROCESS_MEMORY_COUNTERS};
use windows_sys::Win32::System::Shutdown::{
    AbortSystemShutdownW, InitiateSystemShutdownExW, LockWorkStation, SHTDN_REASON_FLAG_PLANNED,
    SHTDN_REASON_MAJOR_OTHER,
};
use windows_sys::Win32::System::SystemInformation::{GlobalMemoryStatusEx, MEMORYSTATUSEX};
use windows_sys::Win32::System::Threading::{
    GetCurrentProcess, GetSystemTimes, OpenProcess, OpenProcessToken, PROCESS_QUERY_INFORMATION,
    PROCESS_TERMINATE, PROCESS_VM_READ, TerminateProcess,
};
use windows_sys::Win32::UI::Input::KeyboardAndMouse::{
    INPUT, INPUT_KEYBOARD, KEYBDINPUT, KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, SendInput,
};
use windows_sys::Win32::UI::WindowsAndMessaging::{
    GetSystemMetrics, HWND_BROADCAST, PostMessageW, SC_MONITORPOWER, SM_CXVIRTUALSCREEN,
    SM_CYVIRTUALSCREEN, SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, WM_SYSCOMMAND,
};

pub struct SystemTelemetry {
    pub cpu_usage: f32,
    pub ram_total_mb: u64,
    pub ram_used_mb: u64,
    pub ram_percent: u32,
    pub battery_percent: Option<u8>,
    pub ac_online: bool,
}

pub struct Win32System;

impl Win32System {
    fn enable_privilege(privilege_name_str: &str) -> Result<(), String> {
        unsafe {
            let mut token = ptr::null_mut();
            if OpenProcessToken(
                GetCurrentProcess(),
                TOKEN_ADJUST_PRIVILEGES | TOKEN_QUERY,
                &mut token,
            ) == 0
            {
                return Err(format!(
                    "OpenProcessToken fehlgeschlagen (Error: {})",
                    GetLastError()
                ));
            }

            let mut luid = std::mem::zeroed();
            let privilege_name: Vec<u16> =
                privilege_name_str.encode_utf16().chain(Some(0)).collect();

            if LookupPrivilegeValueW(ptr::null(), privilege_name.as_ptr(), &mut luid) == 0 {
                CloseHandle(token);
                return Err(format!(
                    "LookupPrivilegeValueW fehlgeschlagen (Error: {})",
                    GetLastError()
                ));
            }

            let mut tp: TOKEN_PRIVILEGES = std::mem::zeroed();
            tp.PrivilegeCount = 1;
            tp.Privileges[0] = LUID_AND_ATTRIBUTES {
                Luid: luid,
                Attributes: SE_PRIVILEGE_ENABLED,
            };

            let result = AdjustTokenPrivileges(
                token,
                0,
                &tp,
                std::mem::size_of::<TOKEN_PRIVILEGES>() as u32,
                ptr::null_mut(),
                ptr::null_mut(),
            );

            CloseHandle(token);

            if result == 0 || GetLastError() != 0 {
                return Err(format!(
                    "AdjustTokenPrivileges fehlgeschlagen (Error: {})",
                    GetLastError()
                ));
            }

            Ok(())
        }
    }

    pub async fn get_system_telemetry() -> Result<SystemTelemetry, &'static str> {
        let (ram_total_mb, ram_used_mb, ram_percent) = unsafe {
            let mut mem_status: MEMORYSTATUSEX = std::mem::zeroed();
            mem_status.dwLength = std::mem::size_of::<MEMORYSTATUSEX>() as u32;
            if GlobalMemoryStatusEx(&mut mem_status) == 0 {
                return Err("Konnte Memory-Status nicht abfragen");
            }
            let total = mem_status.ullTotalPhys / (1024 * 1024);
            let avail = mem_status.ullAvailPhys / (1024 * 1024);
            let used = total.saturating_sub(avail);
            (total, used, mem_status.dwMemoryLoad)
        };

        let (battery_percent, ac_online) = unsafe {
            let mut power_status: SYSTEM_POWER_STATUS = std::mem::zeroed();
            if GetSystemPowerStatus(&mut power_status) != 0 {
                let bat = if power_status.BatteryLifePercent <= 100 {
                    Some(power_status.BatteryLifePercent)
                } else {
                    None
                };
                let ac = power_status.ACLineStatus == 1;
                (bat, ac)
            } else {
                (None, true)
            }
        };

        let cpu_usage = {
            let (idle1, kernel1, user1) = Self::read_system_times()?;
            tokio::time::sleep(std::time::Duration::from_millis(200)).await;
            let (idle2, kernel2, user2) = Self::read_system_times()?;

            let idle_delta = idle2.saturating_sub(idle1);
            let kernel_delta = kernel2.saturating_sub(kernel1);
            let user_delta = user2.saturating_sub(user1);
            let total_system = kernel_delta + user_delta;

            if total_system > 0 {
                let busy = total_system.saturating_sub(idle_delta);
                ((busy as f64 / total_system as f64) * 100.0) as f32
            } else {
                0.0
            }
        };

        Ok(SystemTelemetry {
            cpu_usage,
            ram_total_mb,
            ram_used_mb,
            ram_percent,
            battery_percent,
            ac_online,
        })
    }

    fn read_system_times() -> Result<(u64, u64, u64), &'static str> {
        unsafe {
            let mut idle = std::mem::zeroed();
            let mut kernel = std::mem::zeroed();
            let mut user = std::mem::zeroed();

            if GetSystemTimes(&mut idle, &mut kernel, &mut user) == 0 {
                return Err("GetSystemTimes fehlgeschlagen");
            }

            let to_u64 = |ft: windows_sys::Win32::Foundation::FILETIME| -> u64 {
                ((ft.dwHighDateTime as u64) << 32) | (ft.dwLowDateTime as u64)
            };

            Ok((to_u64(idle), to_u64(kernel), to_u64(user)))
        }
    }

    pub fn get_process_ram_mb(pid: u32) -> u64 {
        unsafe {
            let handle = OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, 0, pid);
            if handle.is_null() {
                return 0;
            }
            let mut pmc: PROCESS_MEMORY_COUNTERS = std::mem::zeroed();
            let mut ram_mb = 0;
            if GetProcessMemoryInfo(
                handle,
                &mut pmc,
                std::mem::size_of::<PROCESS_MEMORY_COUNTERS>() as u32,
            ) != 0
            {
                ram_mb = (pmc.WorkingSetSize / (1024 * 1024)) as u64;
            }
            CloseHandle(handle);
            ram_mb
        }
    }

    pub fn kill_process(pid: u32) -> Result<(), String> {
        if pid <= 4 {
            return Err(
                "Kritische Windows-Systemprozesse (PID <= 4) koennen nicht beendet werden."
                    .to_string(),
            );
        }

        if !std::env::var("ALLOW_REMOTE_PROCESS_TERMINATION")
            .map(|v| matches!(v.trim().to_ascii_lowercase().as_str(), "1" | "true" | "yes"))
            .unwrap_or(false)
        {
            return Err("Remote-Prozessbeendigung ist standardmäßig deaktiviert. Nur nach bewusster lokaler Freigabe über ALLOW_REMOTE_PROCESS_TERMINATION=true aktivieren.".to_string());
        }

        // Do not silently elevate to SeDebugPrivilege. The process can only
        // terminate targets accessible to its current Windows token.
        unsafe {
            let handle = OpenProcess(PROCESS_TERMINATE, 0, pid);
            if handle.is_null() {
                return Err(format!(
                    "OpenProcess fehlgeschlagen (Error: {}). Fehlende Rechte oder PID ungueltig.",
                    GetLastError()
                ));
            }

            let res = TerminateProcess(handle, 1);
            CloseHandle(handle);

            if res != 0 {
                Ok(())
            } else {
                Err(format!(
                    "TerminateProcess fehlgeschlagen (Error: {})",
                    GetLastError()
                ))
            }
        }
    }

    pub fn lock_screen() -> Result<(), &'static str> {
        unsafe {
            if LockWorkStation() != 0 {
                Ok(())
            } else {
                Err("LockWorkStation fehlgeschlagen")
            }
        }
    }

    pub fn suspend_system(hibernate: bool) -> Result<(), &'static str> {
        unsafe {
            let h = if hibernate { 1 } else { 0 };
            if SetSuspendState(h, 0, 0) != 0 {
                Ok(())
            } else {
                Err("SetSuspendState fehlgeschlagen")
            }
        }
    }

    pub fn monitor_off() -> Result<(), &'static str> {
        unsafe {
            let res = PostMessageW(
                HWND_BROADCAST as HWND,
                WM_SYSCOMMAND,
                SC_MONITORPOWER as usize,
                2 as LRESULT,
            );
            if res != 0 {
                Ok(())
            } else {
                Err("Konnte Monitor-Off-Signal nicht senden")
            }
        }
    }

    pub fn initiate_shutdown(reboot: bool, timeout_secs: u32, force: bool) -> Result<(), String> {
        Self::enable_privilege("SeShutdownPrivilege")?;
        unsafe {
            let message: Vec<u16> = if reboot {
                "Terra Remote Engine: Geplanter Neustart eingeleitet...\0"
                    .encode_utf16()
                    .collect()
            } else {
                "Terra Remote Engine: Geplantes Herunterfahren eingeleitet...\0"
                    .encode_utf16()
                    .collect()
            };

            let res = InitiateSystemShutdownExW(
                ptr::null_mut(),
                message.as_ptr() as *mut u16,
                timeout_secs,
                if force { 1 } else { 0 },
                if reboot { 1 } else { 0 },
                SHTDN_REASON_MAJOR_OTHER | SHTDN_REASON_FLAG_PLANNED,
            );

            if res != 0 {
                Ok(())
            } else {
                Err(format!(
                    "InitiateSystemShutdownExW fehlgeschlagen (Error: {})",
                    GetLastError()
                ))
            }
        }
    }

    pub fn abort_shutdown() -> Result<(), String> {
        Self::enable_privilege("SeShutdownPrivilege")?;
        unsafe {
            if AbortSystemShutdownW(ptr::null_mut()) != 0 {
                Ok(())
            } else {
                Err(format!(
                    "AbortSystemShutdownW fehlgeschlagen (Error: {})",
                    GetLastError()
                ))
            }
        }
    }

    fn send_virtual_key(vk_code: u16) {
        unsafe {
            let mut inputs: [INPUT; 2] = std::mem::zeroed();

            inputs[0].r#type = INPUT_KEYBOARD;
            inputs[0].Anonymous.ki = KEYBDINPUT {
                wVk: vk_code,
                wScan: 0,
                dwFlags: KEYEVENTF_EXTENDEDKEY,
                time: 0,
                dwExtraInfo: 0,
            };

            inputs[1].r#type = INPUT_KEYBOARD;
            inputs[1].Anonymous.ki = KEYBDINPUT {
                wVk: vk_code,
                wScan: 0,
                dwFlags: KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP,
                time: 0,
                dwExtraInfo: 0,
            };

            SendInput(2, inputs.as_mut_ptr(), std::mem::size_of::<INPUT>() as i32);
        }
    }

    pub fn volume_up() {
        Self::send_virtual_key(0xAF);
    }
    pub fn volume_down() {
        Self::send_virtual_key(0xAE);
    }
    pub fn volume_mute() {
        Self::send_virtual_key(0xAD);
    }

    // Media Controls
    pub fn media_play_pause() {
        Self::send_virtual_key(0xB3);
    }
    pub fn media_next() {
        Self::send_virtual_key(0xB0);
    }
    pub fn media_prev() {
        Self::send_virtual_key(0xB1);
    }

    // Virtual-desktop screenshot. The bitmap is deselected before GetDIBits:
    // GDI requires that the bitmap not be selected into a device context.
    pub fn take_screenshot() -> Result<Vec<u8>, &'static str> {
        unsafe {
            let x = GetSystemMetrics(SM_XVIRTUALSCREEN);
            let y = GetSystemMetrics(SM_YVIRTUALSCREEN);
            let width = GetSystemMetrics(SM_CXVIRTUALSCREEN);
            let height = GetSystemMetrics(SM_CYVIRTUALSCREEN);

            if width <= 0 || height <= 0 {
                return Err("Ungueltige Bildschirmaufloesung ermittelt");
            }

            let w = width as usize;
            let h = height as usize;
            let row_stride = w
                .checked_mul(3)
                .and_then(|value| value.checked_add(3))
                .map(|value| value & !3)
                .ok_or("Bildschirmabmessungen sind zu gross")?;
            let buffer_size = row_stride
                .checked_mul(h)
                .ok_or("Bildschirmdaten sind zu gross")?;
            let rgb_capacity = w
                .checked_mul(h)
                .and_then(|value| value.checked_mul(3))
                .ok_or("RGB-Bilddaten sind zu gross")?;

            let hdc_screen = GetDC(ptr::null_mut());
            if hdc_screen.is_null() {
                return Err("Konnte Screen DC nicht abgreifen");
            }

            let hdc_mem = CreateCompatibleDC(hdc_screen);
            if hdc_mem.is_null() {
                ReleaseDC(ptr::null_mut(), hdc_screen);
                return Err("Konnte kompatibles DC nicht erstellen");
            }

            let hbitmap = CreateCompatibleBitmap(hdc_screen, width, height);
            if hbitmap.is_null() {
                DeleteDC(hdc_mem);
                ReleaseDC(ptr::null_mut(), hdc_screen);
                return Err("Konnte kompatible Bitmap nicht erstellen");
            }

            let old_obj = SelectObject(hdc_mem, hbitmap);
            if old_obj.is_null() {
                DeleteObject(hbitmap);
                DeleteDC(hdc_mem);
                ReleaseDC(ptr::null_mut(), hdc_screen);
                return Err("Konnte Bitmap nicht in den Speicher-DC einsetzen");
            }

            let blit_ok = BitBlt(hdc_mem, 0, 0, width, height, hdc_screen, x, y, SRCCOPY);
            // Restore the original object before GetDIBits; the bitmap must not
            // remain selected into any device context during that call.
            let _ = SelectObject(hdc_mem, old_obj);

            if blit_ok == 0 {
                DeleteObject(hbitmap);
                DeleteDC(hdc_mem);
                ReleaseDC(ptr::null_mut(), hdc_screen);
                return Err("BitBlt konnte den Bildschirm nicht kopieren");
            }

            let mut bmi: BITMAPINFOHEADER = std::mem::zeroed();
            bmi.biSize = std::mem::size_of::<BITMAPINFOHEADER>() as u32;
            bmi.biWidth = width;
            bmi.biHeight = -height;
            bmi.biPlanes = 1;
            bmi.biBitCount = 24;
            bmi.biCompression = 0;

            let mut bgr_buffer = vec![0u8; buffer_size];
            let lines = GetDIBits(
                hdc_screen,
                hbitmap,
                0,
                height as u32,
                bgr_buffer.as_mut_ptr() as *mut _,
                &mut bmi as *mut _ as *mut _,
                DIB_RGB_COLORS,
            );

            DeleteObject(hbitmap);
            DeleteDC(hdc_mem);
            ReleaseDC(ptr::null_mut(), hdc_screen);

            if lines != height as i32 {
                return Err("GetDIBits konnte nicht alle Bildschirmzeilen lesen");
            }

            // Convert padded BGR rows to tightly packed RGB pixels.
            let mut rgb_pixels = Vec::with_capacity(rgb_capacity);
            for row in 0..h {
                let row_start = row * row_stride;
                for col in 0..w {
                    let pixel = row_start + col * 3;
                    rgb_pixels.push(bgr_buffer[pixel + 2]);
                    rgb_pixels.push(bgr_buffer[pixel + 1]);
                    rgb_pixels.push(bgr_buffer[pixel]);
                }
            }

            let mut png_bytes = Vec::new();
            let cursor = Cursor::new(&mut png_bytes);
            let encoder = PngEncoder::new(cursor);
            encoder
                .write_image(
                    &rgb_pixels,
                    width as u32,
                    height as u32,
                    ColorType::Rgb8.into(),
                )
                .map_err(|_| "PNG-Kompression fehlgeschlagen")?;

            Ok(png_bytes)
        }
    }
}
