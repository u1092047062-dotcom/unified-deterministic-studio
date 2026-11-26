use crate::services::process_monitor::ProcessMonitor;
use crate::services::win32_system::Win32System;
use std::path::{Component, Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use teloxide::{
    prelude::*,
    types::{InlineKeyboardButton, InlineKeyboardMarkup, InputFile},
    utils::command::BotCommands,
};

pub static ALERTS_ENABLED: AtomicBool = AtomicBool::new(true);

#[derive(BotCommands, Clone)]
#[command(rename_rule = "lowercase", description = "Verfügbare Befehle:")]
pub enum Command {
    #[command(description = "Öffnet das interaktive Remote-Dashboard mit Touch-Buttons.")]
    Menu,
    #[command(description = "Zeigt den Systemstatus an.")]
    Status,
    #[command(description = "Liefert Live-Telemetrie (CPU, RAM, Akku).")]
    Stats,
    #[command(description = "Zeigt die Top aktiven Prozesse nach RAM-Verbrauch an.")]
    Ps,
    #[command(description = "Beendet einen Prozess nur nach lokaler Freigabe (z.B. /kill 1234).")]
    Kill(u32),
    #[command(
        description = "Sendet ein Logfile als Datei (/getlog audit, /getlog debug, /getlog eda oder /getlog art)."
    )]
    Getlog(String),
    #[command(
        description = "Zeigt die letzten 20 Zeilen eines Logs an (/tail audit, /tail debug, /tail eda oder /tail art)."
    )]
    Tail(String),
    #[command(description = "Schaltet Push-Alerts ein oder aus (/alerts on oder /alerts off).")]
    Alerts(String),
    #[command(description = "Sperrt das System sofort.")]
    Lock,
    #[command(description = "Schaltet Bildschirme aus (Ghost-Mode).")]
    Monoff,
    #[command(description = "Versetzt das System in den Standby (Sleep).")]
    Sleep,
    #[command(description = "Fährt das System in 30s herunter.")]
    Shutdown,
    #[command(description = "Startet das System in 30s neu.")]
    Reboot,
    #[command(description = "Bricht geplantes Herunterfahren/Neustarten ab.")]
    Abort,
    #[command(description = "Erhöht die Lautstärke.")]
    Volup,
    #[command(description = "Verringert die Lautstärke.")]
    Voldown,
    #[command(description = "Schaltet Audio stumm / hebt Stummschaltung auf.")]
    Mute,
    #[command(description = "Mediensteuerung: Play / Pause.")]
    Playpause,
    #[command(description = "Mediensteuerung: Nächster Titel.")]
    Next,
    #[command(description = "Mediensteuerung: Vorheriger Titel.")]
    Prev,
    #[command(description = "Sendet ein verlustfrei komprimiertes PNG-Livebild des Desktops.")]
    Screen,
}

pub fn build_main_dashboard() -> InlineKeyboardMarkup {
    let rows = vec![
        vec![
            InlineKeyboardButton::callback("📊 Telemetrie", "act_stats"),
            InlineKeyboardButton::callback("📸 Screenshot", "act_screen"),
        ],
        vec![
            InlineKeyboardButton::callback("🔒 Sperren", "act_lock"),
            InlineKeyboardButton::callback("🌑 Monitor Aus", "act_monoff"),
            InlineKeyboardButton::callback("💤 Sleep", "act_sleep"),
        ],
        vec![
            InlineKeyboardButton::callback("🔉 Leiser", "act_voldown"),
            InlineKeyboardButton::callback("🔇 Mute", "act_mute"),
            InlineKeyboardButton::callback("🔊 Lauter", "act_volup"),
        ],
        vec![
            InlineKeyboardButton::callback("⏮ Zurück", "act_prev"),
            InlineKeyboardButton::callback("⏯ Play/Pause", "act_playpause"),
            InlineKeyboardButton::callback("⏭ Weiter", "act_next"),
        ],
        vec![
            InlineKeyboardButton::callback("🛑 Shutdown...", "ask_shutdown"),
            InlineKeyboardButton::callback("🔄 Reboot...", "ask_reboot"),
        ],
    ];
    InlineKeyboardMarkup::new(rows)
}

pub fn build_confirm_keyboard(action: &str) -> InlineKeyboardMarkup {
    let confirm_data = format!("confirm_{}", action);
    let rows = vec![vec![
        InlineKeyboardButton::callback("✅ Ja, Ausführen", confirm_data),
        InlineKeyboardButton::callback("❌ Abbrechen", "act_cancel"),
    ]];
    InlineKeyboardMarkup::new(rows)
}

fn remote_screen_capture_enabled() -> bool {
    std::env::var("ALLOW_REMOTE_SCREEN_CAPTURE")
        .map(|value| {
            matches!(
                value.trim().to_ascii_lowercase().as_str(),
                "1" | "true" | "yes"
            )
        })
        .unwrap_or(false)
}

fn validate_pipeline_log_path(raw: &str, label: &str, env_var: &str) -> Result<PathBuf, String> {
    let trimmed = raw.trim();
    if trimmed.is_empty() {
        return Err(format!("{} ist nicht konfiguriert oder leer.", env_var));
    }

    let candidate = Path::new(trimmed);
    if candidate
        .components()
        .any(|component| matches!(component, Component::ParentDir))
    {
        return Err(format!(
            "Ungültiger {}-Logpfad: relative Pfadnavigation ('..') ist nicht zulässig.",
            label
        ));
    }

    let file_name = candidate
        .file_name()
        .and_then(|name| name.to_str())
        .ok_or_else(|| format!("Ungültiger {}-Logpfad: kein Dateiname angegeben.", label))?;
    if file_name.to_ascii_lowercase().starts_with(".env") {
        return Err("Zugriff verweigert: Konfigurationsdateien (.env*) sind gesperrt.".to_string());
    }

    let ext = candidate
        .extension()
        .and_then(|ext| ext.to_str())
        .map(|ext| ext.to_ascii_lowercase());
    if !matches!(ext.as_deref(), Some("log" | "jsonl" | "txt")) {
        return Err(format!(
            "Ungültige {}-Logdatei: nur .log, .jsonl oder .txt sind zulässig.",
            label
        ));
    }

    let canonical = std::fs::canonicalize(candidate)
        .map_err(|_| format!("{}-Logdatei wurde nicht gefunden: {}", label, trimmed))?;
    if !canonical.is_file() {
        return Err(format!(
            "{}-Logpfad ist keine reguläre Datei: {}",
            label, trimmed
        ));
    }

    Ok(canonical)
}

fn validate_eda_log_path(raw: &str) -> Result<PathBuf, String> {
    validate_pipeline_log_path(raw, "EDA", "EDA_PIPELINE_LOG_PATH")
}

fn validate_art_log_path(raw: &str) -> Result<PathBuf, String> {
    validate_pipeline_log_path(raw, "ART", "ART_PIPELINE_LOG_PATH")
}

fn resolve_eda_pipeline_log_path() -> Result<PathBuf, String> {
    let raw = std::env::var("EDA_PIPELINE_LOG_PATH").map_err(|_| {
        "EDA_PIPELINE_LOG_PATH ist nicht gesetzt. Bitte in der lokalen .env konfigurieren."
            .to_string()
    })?;
    validate_eda_log_path(&raw)
}

fn resolve_art_pipeline_log_path() -> Result<PathBuf, String> {
    let raw = std::env::var("ART_PIPELINE_LOG_PATH").map_err(|_| {
        "ART_PIPELINE_LOG_PATH ist nicht gesetzt. Bitte in der lokalen .env konfigurieren."
            .to_string()
    })?;
    validate_art_log_path(&raw)
}

pub async fn handle_command(
    bot: Bot,
    msg: Message,
    cmd: Command,
    allowed_id: u64,
) -> ResponseResult<()> {
    if msg.chat.id.0 != allowed_id as i64
        || msg.from.as_ref().map(|user| user.id.0) != Some(allowed_id)
    {
        let _ = bot
            .send_message(msg.chat.id, "⛔ Zugriff verweigert!")
            .await;
        return Ok(());
    }

    match cmd {
        Command::Menu => {
            let keyboard = build_main_dashboard();
            let _ = bot
                .send_message(
                    msg.chat.id,
                    "🎛 **TERRA REMOTE DASHBOARD**\nWähle eine Aktion:",
                )
                .reply_markup(keyboard)
                .await;
        }
        Command::Status => {
            let alerts_active = ALERTS_ENABLED.load(Ordering::Relaxed);
            let state_str = if alerts_active {
                "aktiviert"
            } else {
                "deaktiviert"
            };
            let text = format!(
                "🟢 Terra Engine läuft stabil.\n📡 Echtzeit-Alerts: {}",
                state_str
            );
            let _ = bot.send_message(msg.chat.id, text).await;
        }
        Command::Stats => {
            let _ = bot
                .send_message(msg.chat.id, "📊 Erfasse Hardware-Telemetrie...")
                .await;
            match Win32System::get_system_telemetry().await {
                Ok(data) => {
                    let battery_info = match data.battery_percent {
                        Some(pct) => format!(
                            "{}% ({})",
                            pct,
                            if data.ac_online {
                                "⚡ Am Stromnetz"
                            } else {
                                "🔋 Akkubetrieb"
                            }
                        ),
                        None => {
                            if data.ac_online {
                                "⚡ Desktop / Netzbetrieb (Kein Akku)".to_string()
                            } else {
                                "Unbekannt".to_string()
                            }
                        }
                    };

                    let response = format!(
                        "🖥 **HARDWARE TELEMETRIE**\n                         ━━━━━━━━━━━━━━━━━━━\n                         ⚙️ **CPU:** {:.1}%\n                         🧠 **RAM:** {} MB / {} MB ({}%)\n                         🔌 **Strom:** {}\n                         ━━━━━━━━━━━━━━━━━━━",
                        data.cpu_usage,
                        data.ram_used_mb,
                        data.ram_total_mb,
                        data.ram_percent,
                        battery_info
                    );
                    let _ = bot.send_message(msg.chat.id, response).await;
                }
                Err(e) => {
                    let _ = bot
                        .send_message(
                            msg.chat.id,
                            format!("❌ Fehler beim Ermitteln der Telemetrie: {}", e),
                        )
                        .await;
                }
            }
        }
        Command::Ps => {
            let monitor = ProcessMonitor::new();
            let table = monitor.get_current_process_table();

            let mut list: Vec<(u32, String, u64)> = table
                .into_iter()
                .map(|(pid, name)| {
                    let ram = Win32System::get_process_ram_mb(pid);
                    (pid, name, ram)
                })
                .collect();

            list.sort_by_key(|a| std::cmp::Reverse(a.2));
            let top_15: Vec<&(u32, String, u64)> = list.iter().take(15).collect();

            let mut out = String::from(
                "📋 **TOP PROZESSE (NACH RAM)**\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n`PID    | RAM    | PROGRAMM`\n",
            );
            for (pid, name, ram) in top_15 {
                let truncated_name: String = name.chars().take(18).collect();
                out.push_str(&format!(
                    "`{:<6} | {:>4}MB | {:<18}`\n",
                    pid, ram, truncated_name
                ));
            }
            out.push_str("━━━━━━━━━━━━━━━━━━━━━━━━━━\nBeenden ist standardmäßig lokal gesperrt; Freigabe nur über ALLOW_REMOTE_PROCESS_TERMINATION.");

            let _ = bot.send_message(msg.chat.id, out).await;
        }
        Command::Kill(pid) => match Win32System::kill_process(pid) {
            Ok(_) => {
                let _ = bot
                    .send_message(
                        msg.chat.id,
                        format!("🎯 Prozess mit PID `{}` erfolgreich beendet.", pid),
                    )
                    .await;
            }
            Err(e) => {
                let _ = bot
                    .send_message(
                        msg.chat.id,
                        format!("❌ Fehler beim Beenden von PID `{}`: {}", pid, e),
                    )
                    .await;
            }
        },
        Command::Getlog(target) => {
            let target_clean = target.trim().to_lowercase();
            if target_clean == "eda" || target_clean == "art" {
                let resolved = if target_clean == "eda" {
                    resolve_eda_pipeline_log_path()
                } else {
                    resolve_art_pipeline_log_path()
                };
                match resolved {
                    Ok(log_path) => {
                        let _ = bot
                            .send_document(msg.chat.id, InputFile::file(log_path))
                            .await;
                    }
                    Err(err) => {
                        let _ = bot.send_message(msg.chat.id, format!("❌ {}", err)).await;
                    }
                }
                return Ok(());
            }

            let filename = match target_clean.as_str() {
                "audit" => "process_audit.log",
                "debug" => "bot_debug.log",
                _ => {
                    let _ = bot
                        .send_message(
                            msg.chat.id,
                            "⚠️ Ungültiges Logfile! Nutzung: `/getlog audit`, `/getlog debug`, `/getlog eda` oder `/getlog art`",
                        )
                        .await;
                    return Ok(());
                }
            };

            let mut log_path =
                std::env::current_exe().unwrap_or_else(|_| std::path::PathBuf::from("."));
            log_path.pop();
            let full_path = log_path.join(filename);

            if full_path.exists() {
                let _ = bot
                    .send_document(msg.chat.id, InputFile::file(full_path))
                    .await;
            } else {
                let local_path = std::path::PathBuf::from(filename);
                if local_path.exists() {
                    let _ = bot
                        .send_document(msg.chat.id, InputFile::file(local_path))
                        .await;
                } else {
                    let _ = bot
                        .send_message(
                            msg.chat.id,
                            format!("❌ Logdatei `{}` existiert noch nicht.", filename),
                        )
                        .await;
                }
            }
        }
        Command::Tail(target) => {
            let target_clean = target.trim().to_lowercase();
            let (filename, full_path) = if target_clean == "eda" || target_clean == "art" {
                let (resolved, fallback_name) = if target_clean == "eda" {
                    (resolve_eda_pipeline_log_path(), "eda_pipeline.log")
                } else {
                    (resolve_art_pipeline_log_path(), "art_pipeline.log")
                };
                match resolved {
                    Ok(pipeline_path) => {
                        let display_name = pipeline_path
                            .file_name()
                            .and_then(|name| name.to_str())
                            .unwrap_or(fallback_name)
                            .to_string();
                        (display_name, pipeline_path)
                    }
                    Err(err) => {
                        let _ = bot.send_message(msg.chat.id, format!("❌ {}", err)).await;
                        return Ok(());
                    }
                }
            } else {
                let filename = match target_clean.as_str() {
                    "audit" => "process_audit.log",
                    "debug" => "bot_debug.log",
                    _ => {
                        let _ = bot
                            .send_message(
                                msg.chat.id,
                                "⚠️ Ungültiges Logfile! Nutzung: `/tail audit`, `/tail debug`, `/tail eda` oder `/tail art`",
                            )
                            .await;
                        return Ok(());
                    }
                };

                let mut log_path =
                    std::env::current_exe().unwrap_or_else(|_| std::path::PathBuf::from("."));
                log_path.pop();
                let mut resolved = log_path.join(filename);
                if !resolved.exists() {
                    resolved = std::path::PathBuf::from(filename);
                }
                (filename.to_string(), resolved)
            };

            match tokio::fs::read_to_string(&full_path).await {
                Ok(content) => {
                    let lines: Vec<&str> = content.lines().collect();
                    let last_lines: Vec<&str> =
                        lines.iter().rev().take(20).rev().copied().collect();
                    if last_lines.is_empty() {
                        let _ = bot
                            .send_message(
                                msg.chat.id,
                                format!("ℹ️ Logdatei `{}` ist leer.", filename),
                            )
                            .await;
                    } else {
                        let joined = last_lines.join("\n");
                        let text = format!(
                            "📄 **Letzte Einträge aus `{}`:**\n```\n{}\n```",
                            filename, joined
                        );
                        let _ = bot.send_message(msg.chat.id, text).await;
                    }
                }
                Err(e) => {
                    let _ = bot
                        .send_message(
                            msg.chat.id,
                            format!("❌ Konnte `{}` nicht lesen: {}", filename, e),
                        )
                        .await;
                }
            }
        }
        Command::Alerts(param) => {
            let param_clean = param.trim().to_lowercase();
            match param_clean.as_str() {
                "on" | "ein" | "true" => {
                    ALERTS_ENABLED.store(true, Ordering::Relaxed);
                    let _ = bot
                        .send_message(msg.chat.id, "🔔 Push-Alerts sind jetzt AKTIVIERT.")
                        .await;
                }
                "off" | "aus" | "false" => {
                    ALERTS_ENABLED.store(false, Ordering::Relaxed);
                    let _ = bot
                        .send_message(msg.chat.id, "🔕 Push-Alerts sind jetzt PAUSIERT.")
                        .await;
                }
                _ => {
                    let current = if ALERTS_ENABLED.load(Ordering::Relaxed) {
                        "aktiviert"
                    } else {
                        "pausiert"
                    };
                    let _ = bot
                        .send_message(
                            msg.chat.id,
                            format!(
                                "Aktueller Status: {}\nNutzung: /alerts on oder /alerts off",
                                current
                            ),
                        )
                        .await;
                }
            }
        }
        Command::Lock => match Win32System::lock_screen() {
            Ok(()) => {
                let _ = bot
                    .send_message(msg.chat.id, "🔒 Windows-Sperre wurde an Windows übergeben.")
                    .await;
            }
            Err(error) => {
                let _ = bot
                    .send_message(
                        msg.chat.id,
                        format!("❌ Windows-Sperre fehlgeschlagen: {}", error),
                    )
                    .await;
            }
        },
        Command::Monoff => match Win32System::monitor_off() {
            Ok(_) => {
                let _ = bot
                    .send_message(msg.chat.id, "🌑 Displays abgeschaltet (Ghost-Mode).")
                    .await;
            }
            Err(e) => {
                let _ = bot
                    .send_message(msg.chat.id, format!("❌ Fehler: {}", e))
                    .await;
            }
        },
        Command::Sleep => match Win32System::suspend_system(false) {
            Ok(()) => {
                let _ = bot
                    .send_message(
                        msg.chat.id,
                        "💤 Energiesparmodus wurde an Windows übergeben.",
                    )
                    .await;
            }
            Err(error) => {
                let _ = bot
                    .send_message(
                        msg.chat.id,
                        format!("❌ Energiesparmodus fehlgeschlagen: {}", error),
                    )
                    .await;
            }
        },
        Command::Shutdown | Command::Reboot => {
            let _ = bot.send_message(msg.chat.id, "⚠️ Bitte /menu öffnen und Herunterfahren/Neustart dort über die zweistufige Bestätigung auslösen.").await;
        }
        Command::Abort => match Win32System::abort_shutdown() {
            Ok(_) => {
                let _ = bot
                    .send_message(
                        msg.chat.id,
                        "✅ Herunterfahren / Neustart erfolgreich abgebrochen.",
                    )
                    .await;
            }
            Err(e) => {
                let _ = bot
                    .send_message(msg.chat.id, format!("❌ Abbruch fehlgeschlagen: {}", e))
                    .await;
            }
        },
        Command::Volup => {
            Win32System::volume_up();
            let _ = bot.send_message(msg.chat.id, "🔊 Lautstärke +").await;
        }
        Command::Voldown => {
            Win32System::volume_down();
            let _ = bot.send_message(msg.chat.id, "🔉 Lautstärke -").await;
        }
        Command::Mute => {
            Win32System::volume_mute();
            let _ = bot
                .send_message(msg.chat.id, "🔇 Audio Mute umgeschaltet.")
                .await;
        }
        Command::Playpause => {
            Win32System::media_play_pause();
            let _ = bot
                .send_message(msg.chat.id, "⏯ Play / Pause gesendet.")
                .await;
        }
        Command::Next => {
            Win32System::media_next();
            let _ = bot.send_message(msg.chat.id, "⏭ Nächster Titel.").await;
        }
        Command::Prev => {
            Win32System::media_prev();
            let _ = bot.send_message(msg.chat.id, "⏮ Vorheriger Titel.").await;
        }
        Command::Screen => {
            if !remote_screen_capture_enabled() {
                let _ = bot.send_message(msg.chat.id, "🔒 Fern-Screenshots sind standardmäßig deaktiviert. Eine bewusste lokale Freigabe ist über ALLOW_REMOTE_SCREEN_CAPTURE=true erforderlich.").await;
                return Ok(());
            }
            let _ = bot
                .send_message(msg.chat.id, "📸 Erstelle PNG-Livebild...")
                .await;
            match Win32System::take_screenshot() {
                Ok(png_data) => {
                    let file = InputFile::memory(png_data).file_name("screenshot.png");
                    let _ = bot.send_photo(msg.chat.id, file).await;
                }
                Err(e) => {
                    let _ = bot
                        .send_message(msg.chat.id, format!("❌ Fehler beim Screenshot: {}", e))
                        .await;
                }
            }
        }
    }
    Ok(())
}

pub async fn handle_callback(bot: Bot, q: CallbackQuery, allowed_id: u64) -> ResponseResult<()> {
    if q.from.id.0 != allowed_id
        || q.message.as_ref().map(|message| message.chat().id.0) != Some(allowed_id as i64)
    {
        let _ = bot
            .answer_callback_query(q.id.clone())
            .text("⛔ Zugriff verweigert!")
            .await;
        return Ok(());
    }

    let data = match q.data {
        Some(d) => d,
        None => return Ok(()),
    };

    let chat_id = match q.message {
        Some(ref m) => m.chat().id,
        None => return Ok(()),
    };

    let message_id = match q.message {
        Some(ref m) => m.id(),
        None => return Ok(()),
    };

    match data.as_str() {
        "act_stats" => {
            let _ = bot
                .answer_callback_query(q.id.clone())
                .text("Lade Telemetrie...")
                .await;
            match Win32System::get_system_telemetry().await {
                Ok(t) => {
                    let bat = match t.battery_percent {
                        Some(p) => format!("{}%", p),
                        None => {
                            if t.ac_online {
                                "Netz".to_string()
                            } else {
                                "N/A".to_string()
                            }
                        }
                    };
                    let text = format!(
                        "📊 **LIVE TELEMETRIE**\n• CPU: {:.1}%\n• RAM: {}/{} MB ({}%)\n• Strom: {}",
                        t.cpu_usage, t.ram_used_mb, t.ram_total_mb, t.ram_percent, bat
                    );
                    let _ = bot
                        .edit_message_text(chat_id, message_id, text)
                        .reply_markup(build_main_dashboard())
                        .await;
                }
                Err(e) => {
                    let _ = bot.send_message(chat_id, format!("❌ Fehler: {}", e)).await;
                }
            }
        }
        "act_screen" => {
            if !remote_screen_capture_enabled() {
                let _ = bot
                    .answer_callback_query(q.id.clone())
                    .text("Fern-Screenshots sind lokal nicht freigegeben.")
                    .await;
                return Ok(());
            }
            let _ = bot
                .answer_callback_query(q.id.clone())
                .text("Erstelle Screenshot...")
                .await;
            match Win32System::take_screenshot() {
                Ok(png_data) => {
                    let file = InputFile::memory(png_data).file_name("screenshot.png");
                    let _ = bot.send_photo(chat_id, file).await;
                }
                Err(e) => {
                    let _ = bot
                        .send_message(chat_id, format!("❌ Screenshot fehlgeschlagen: {}", e))
                        .await;
                }
            }
        }
        "act_lock" => {
            let _ = bot
                .answer_callback_query(q.id.clone())
                .text("Sperre Windows...")
                .await;
            let result = Win32System::lock_screen();
            let response = match result {
                Ok(()) => "🔒 Windows-Sperre wurde an Windows übergeben.".to_string(),
                Err(error) => format!("❌ Windows-Sperre fehlgeschlagen: {}", error),
            };
            let _ = bot
                .edit_message_text(chat_id, message_id, response)
                .reply_markup(build_main_dashboard())
                .await;
        }
        "act_monoff" => {
            let _ = bot
                .answer_callback_query(q.id.clone())
                .text("Monitore aus...")
                .await;
            let _ = Win32System::monitor_off();
            let _ = bot
                .edit_message_text(
                    chat_id,
                    message_id,
                    "🌑 **Displays abgeschaltet (Ghost-Mode).**",
                )
                .reply_markup(build_main_dashboard())
                .await;
        }
        "act_sleep" => {
            let _ = bot
                .answer_callback_query(q.id.clone())
                .text("Standby wird aktiviert...")
                .await;
            let result = Win32System::suspend_system(false);
            let response = match result {
                Ok(()) => "💤 Energiesparmodus wurde an Windows übergeben.".to_string(),
                Err(error) => format!("❌ Energiesparmodus fehlgeschlagen: {}", error),
            };
            let _ = bot
                .edit_message_text(chat_id, message_id, response)
                .reply_markup(build_main_dashboard())
                .await;
        }
        "act_volup" => {
            Win32System::volume_up();
            let _ = bot
                .answer_callback_query(q.id.clone())
                .text("🔊 Lauter")
                .await;
        }
        "act_voldown" => {
            Win32System::volume_down();
            let _ = bot
                .answer_callback_query(q.id.clone())
                .text("🔉 Leiser")
                .await;
        }
        "act_mute" => {
            Win32System::volume_mute();
            let _ = bot
                .answer_callback_query(q.id.clone())
                .text("🔇 Mute")
                .await;
        }
        "act_playpause" => {
            Win32System::media_play_pause();
            let _ = bot
                .answer_callback_query(q.id.clone())
                .text("⏯ Play/Pause")
                .await;
        }
        "act_next" => {
            Win32System::media_next();
            let _ = bot
                .answer_callback_query(q.id.clone())
                .text("⏭ Nächster Titel")
                .await;
        }
        "act_prev" => {
            Win32System::media_prev();
            let _ = bot
                .answer_callback_query(q.id.clone())
                .text("⏮ Vorheriger Titel")
                .await;
        }
        "ask_shutdown" => {
            let _ = bot.answer_callback_query(q.id.clone()).await;
            let kb = build_confirm_keyboard("shutdown");
            let _ = bot.edit_message_text(chat_id, message_id, "⚠️ **SICHERHEITSABFRAGE:**\nSoll Windows 11 wirklich in 30 Sekunden heruntergefahren werden?")
                .reply_markup(kb)
                .await;
        }
        "ask_reboot" => {
            let _ = bot.answer_callback_query(q.id.clone()).await;
            let kb = build_confirm_keyboard("reboot");
            let _ = bot
                .edit_message_text(
                    chat_id,
                    message_id,
                    "⚠️ **SICHERHEITSABFRAGE:**\nSoll das System wirklich neu gestartet werden?",
                )
                .reply_markup(kb)
                .await;
        }
        "confirm_shutdown" => {
            let result = Win32System::initiate_shutdown(false, 30, false);
            let response = match result {
                Ok(()) => "🛑 Shutdown wurde an Windows übergeben. PC fährt in 30 Sekunden herunter; /abort versucht den Abbruch.".to_string(),
                Err(error) => format!("❌ Shutdown konnte nicht gestartet werden: {}", error),
            };
            let _ = bot
                .answer_callback_query(q.id.clone())
                .text("Shutdown-Anforderung verarbeitet.")
                .await;
            let _ = bot
                .edit_message_text(chat_id, message_id, response)
                .reply_markup(build_main_dashboard())
                .await;
        }
        "confirm_reboot" => {
            let result = Win32System::initiate_shutdown(true, 30, false);
            let response = match result {
                Ok(()) => "🔄 Neustart wurde an Windows übergeben. PC startet in 30 Sekunden neu; /abort versucht den Abbruch.".to_string(),
                Err(error) => format!("❌ Neustart konnte nicht gestartet werden: {}", error),
            };
            let _ = bot
                .answer_callback_query(q.id.clone())
                .text("Neustart-Anforderung verarbeitet.")
                .await;
            let _ = bot
                .edit_message_text(chat_id, message_id, response)
                .reply_markup(build_main_dashboard())
                .await;
        }
        "act_cancel" => {
            let _ = bot
                .answer_callback_query(q.id.clone())
                .text("Aktion abgebrochen.")
                .await;
            let _ = bot
                .edit_message_text(
                    chat_id,
                    message_id,
                    "🎛 **TERRA REMOTE DASHBOARD**\nWähle eine Aktion:",
                )
                .reply_markup(build_main_dashboard())
                .await;
        }
        _ => {
            let _ = bot.answer_callback_query(q.id.clone()).await;
        }
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn eda_log_path_rejects_empty_and_parent_traversal() {
        assert!(validate_eda_log_path("   ").is_err());
        assert!(validate_eda_log_path("../logs/eda_pipeline.log").is_err());
        assert!(validate_eda_log_path("logs/../../secret.log").is_err());
    }

    #[test]
    fn eda_log_path_rejects_secret_and_non_log_files() {
        assert!(validate_eda_log_path(".env").is_err());
        assert!(validate_eda_log_path("C:\\temp\\.env.backup").is_err());
        assert!(validate_eda_log_path("C:\\temp\\credentials.json").is_err());
    }

    #[test]
    fn eda_log_path_canonicalizes_existing_log_file() {
        let temp_file =
            std::env::temp_dir().join(format!("terra_eda_log_test_{}.log", std::process::id()));
        std::fs::write(&temp_file, "test log line\n").expect("Temp-Logdatei anlegen");

        let resolved = validate_eda_log_path(temp_file.to_str().expect("UTF-8 Pfad"));
        let _ = std::fs::remove_file(&temp_file);

        let canonical = resolved.expect("Vorhandene .log-Datei muss akzeptiert werden");
        assert!(canonical.ends_with(format!("terra_eda_log_test_{}.log", std::process::id())));
    }

    #[test]
    fn art_log_path_rejects_traversal_and_accepts_existing_jsonl() {
        assert!(validate_art_log_path("../art_pipeline.log").is_err());
        assert!(validate_art_log_path(".env.production").is_err());

        let temp_file =
            std::env::temp_dir().join(format!("terra_art_log_test_{}.jsonl", std::process::id()));
        std::fs::write(&temp_file, "{\"event\":\"ok\"}\n").expect("Temp-JSONL anlegen");

        let resolved = validate_art_log_path(temp_file.to_str().expect("UTF-8 Pfad"));
        let _ = std::fs::remove_file(&temp_file);

        let canonical = resolved.expect("Vorhandene .jsonl-Datei muss akzeptiert werden");
        assert!(canonical.ends_with(format!("terra_art_log_test_{}.jsonl", std::process::id())));
    }
}
