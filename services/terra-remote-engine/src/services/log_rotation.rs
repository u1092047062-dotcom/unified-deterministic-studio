use std::fs::{self, File, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};

/// Groessenbasierter rotierender Log-Writer (Standard: 5 MB, 3 Backups) fuer simplelog::WriteLogger.
/// Verhindert das Ueberschreiben alter Logs beim Neustart sowie unbegrenztes Anwachsen auf C:.
pub struct RotatingLogWriter {
    base_path: PathBuf,
    max_bytes: u64,
    backup_count: usize,
    current_size: u64,
    file: File,
}

impl RotatingLogWriter {
    pub fn open(
        base_path: impl AsRef<Path>,
        max_bytes: u64,
        backup_count: usize,
    ) -> io::Result<Self> {
        let base_path = base_path.as_ref().to_path_buf();
        if let Some(parent) = base_path.parent()
            && !parent.as_os_str().is_empty()
        {
            fs::create_dir_all(parent)?;
        }
        let file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&base_path)?;
        let current_size = file.metadata().map(|m| m.len()).unwrap_or(0);

        Ok(Self {
            base_path,
            max_bytes: max_bytes.max(128),
            backup_count,
            current_size,
            file,
        })
    }

    fn backup_path(&self, index: usize) -> PathBuf {
        let mut name = self.base_path.as_os_str().to_os_string();
        name.push(format!(".{}", index));
        PathBuf::from(name)
    }

    fn rotate(&mut self) -> io::Result<()> {
        self.file.flush()?;
        if self.backup_count > 0 {
            let oldest = self.backup_path(self.backup_count);
            if oldest.exists() {
                let _ = fs::remove_file(&oldest);
            }
            for idx in (1..self.backup_count).rev() {
                let src = self.backup_path(idx);
                let dst = self.backup_path(idx + 1);
                if src.exists() {
                    let _ = fs::rename(&src, &dst);
                }
            }
            // Aktuelle Datei schliessen ueber Ersetzung mit temporaerem Handle vor rename auf Windows
            let first_backup = self.backup_path(1);
            let tmp_empty = self.backup_path(self.backup_count + 99);
            let placeholder = OpenOptions::new()
                .create(true)
                .write(true)
                .truncate(true)
                .open(&tmp_empty)?;
            let old_file = std::mem::replace(&mut self.file, placeholder);
            drop(old_file);
            if self.base_path.exists() {
                let _ = fs::rename(&self.base_path, &first_backup);
            }
            let _ = fs::remove_file(&tmp_empty);
        }

        self.file = OpenOptions::new()
            .create(true)
            .write(true)
            .truncate(true)
            .open(&self.base_path)?;
        self.current_size = 0;
        Ok(())
    }
}

impl Write for RotatingLogWriter {
    fn write(&mut self, buf: &[u8]) -> io::Result<usize> {
        if self.current_size.saturating_add(buf.len() as u64) > self.max_bytes
            && self.current_size > 0
        {
            self.rotate()?;
        }
        let written = self.file.write(buf)?;
        self.current_size = self.current_size.saturating_add(written as u64);
        Ok(written)
    }

    fn flush(&mut self) -> io::Result<()> {
        self.file.flush()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rotating_writer_rotates_and_caps_backups() {
        let dir = std::env::temp_dir().join(format!("terra_rot_test_{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        let base = dir.join("bot_debug.log");

        {
            let mut writer = RotatingLogWriter::open(&base, 160, 2).expect("open writer");
            for i in 0..12 {
                writeln!(
                    writer,
                    "Log-Zeile Nummer {:03} mit etwas Inhalt fuer Rotation",
                    i
                )
                .expect("write line");
            }
            writer.flush().expect("flush");
        }

        assert!(base.is_file());
        assert!(PathBuf::from(format!("{}.1", base.display())).is_file());
        assert!(!PathBuf::from(format!("{}.3", base.display())).exists());
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn rotating_writer_appends_without_overwriting_existing_log() {
        let dir = std::env::temp_dir().join(format!("terra_append_test_{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        let base = dir.join("bot_debug.log");

        {
            let mut w1 = RotatingLogWriter::open(&base, 4096, 3).expect("open w1");
            writeln!(w1, "ERSTE_SITZUNG").expect("write 1");
        }
        {
            let mut w2 = RotatingLogWriter::open(&base, 4096, 3).expect("open w2");
            writeln!(w2, "ZWEITE_SITZUNG").expect("write 2");
        }

        let text = fs::read_to_string(&base).expect("read log");
        assert!(text.contains("ERSTE_SITZUNG"));
        assert!(text.contains("ZWEITE_SITZUNG"));
        let _ = fs::remove_dir_all(&dir);
    }
}
