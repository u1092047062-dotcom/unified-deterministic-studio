#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod handlers;
mod services;

use handlers::telegram::{ALERTS_ENABLED, Command, handle_callback, handle_command};
use services::log_rotation::RotatingLogWriter;
use services::process_monitor::{AuditEvent, ProcessMonitor};
use simplelog::{Config, LevelFilter, WriteLogger};
use std::env;
use std::sync::atomic::Ordering;
use std::time::Duration;
use teloxide::prelude::*;
use tokio::sync::mpsc;

async fn start_audit_watcher(tx: mpsc::Sender<AuditEvent>) {
    let mut monitor = ProcessMonitor::new();
    loop {
        match monitor.check_system().await {
            Ok(events) => {
                for ev in events {
                    if tx.send(ev).await.is_err() {
                        log::warn!(
                            "Process-Audit: Alert-Kanal geschlossen; Ereignis konnte nicht zugestellt werden."
                        );
                        break;
                    }
                }
            }
            Err(error) => {
                // A failed snapshot must never be interpreted as all processes having stopped.
                log::error!("Process-Monitor: Systemprüfung fehlgeschlagen: {}", error);
            }
        }
        tokio::time::sleep(Duration::from_secs(5)).await;
    }
}

async fn start_alert_dispatcher(bot: Bot, chat_id: u64, mut rx: mpsc::Receiver<AuditEvent>) {
    let target_chat = ChatId(chat_id as i64);
    while let Some(event) = rx.recv().await {
        if !ALERTS_ENABLED.load(Ordering::Relaxed) {
            continue;
        }

        match event {
            AuditEvent::Started {
                pid,
                name,
                time,
                is_priority,
            } => {
                if is_priority {
                    let msg = format!(
                        "⚠️ ALERT: Kritischer Prozess gestartet!\nProgramm: {}\nPID: {}\nZeit: {}",
                        name,
                        pid,
                        time.format("%H:%M:%S")
                    );
                    let _ = bot.send_message(target_chat, msg).await;
                }
            }
            AuditEvent::Stopped {
                pid,
                name,
                start_time,
                end_time,
                duration_str,
                is_priority,
            } => {
                if is_priority {
                    let msg = format!(
                        "ℹ️ Prozess beendet:\nProgramm: {}\nPID: {}\nStart: {}\nEnde: {}\nDauer: {}",
                        name,
                        pid,
                        start_time.format("%Y-%m-%d %H:%M:%S"),
                        end_time.format("%Y-%m-%d %H:%M:%S"),
                        duration_str
                    );
                    let _ = bot.send_message(target_chat, msg).await;
                }
            }
        }
    }
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    // ============================================================
    // TERRA REMOTE ENGINE 1.1
    // SICHERE ENV-INITIALISIERUNG
    // ============================================================

    let exe_path = env::current_exe()?;

    let exe_dir = exe_path.parent().ok_or_else(|| {
        std::io::Error::new(
            std::io::ErrorKind::NotFound,
            "EXE-Verzeichnis konnte nicht ermittelt werden",
        )
    })?;

    // Mögliche Speicherorte der .env-Datei
    let env_candidates = [
        exe_dir.join(".env"),
        exe_dir.join("../.env"),
        exe_dir.join("../../.env"),
    ];

    // Erste tatsächlich vorhandene .env-Datei ermitteln
    let env_path = env_candidates
        .iter()
        .find(|path| path.is_file())
        .ok_or_else(|| {
            std::io::Error::new(
                std::io::ErrorKind::NotFound,
                "Keine .env-Datei an den erwarteten Speicherorten gefunden",
            )
        })?;

    // Konfiguration laden
    dotenvy::from_path(env_path)?;

    // Erfolgreiche Initialisierung protokollieren
    eprintln!(
        "Terra Remote Engine: ENV-Konfiguration geladen: {}",
        env_path.display()
    );

    // ============================================================
    // ENDE SICHERE ENV-INITIALISIERUNG
    // ============================================================

    let mut log_path = env::current_exe().unwrap_or_else(|_| std::path::PathBuf::from("."));
    log_path.pop();
    const DEBUG_LOG_MAX_BYTES: u64 = 5 * 1024 * 1024;
    const DEBUG_LOG_BACKUPS: usize = 3;
    let log_file = RotatingLogWriter::open(
        log_path.join("bot_debug.log"),
        DEBUG_LOG_MAX_BYTES,
        DEBUG_LOG_BACKUPS,
    )
    .or_else(|_| RotatingLogWriter::open("bot_debug.log", DEBUG_LOG_MAX_BYTES, DEBUG_LOG_BACKUPS));

    match log_file {
        Ok(file) => {
            if let Err(error) = WriteLogger::init(LevelFilter::Info, Config::default(), file) {
                eprintln!("Logger konnte nicht initialisiert werden: {}", error);
            }
        }
        Err(error) => {
            eprintln!("Debug-Log konnte nicht geöffnet werden: {}", error);
        }
    }

    log::info!("Starting Terra Remote Engine v1.2.0 (development candidate)...");

    let token =
        env::var("TELOXIDE_TOKEN").map_err(|_| "TELOXIDE_TOKEN fehlt in der lokalen .env-Datei")?;
    if token.trim().is_empty() {
        return Err("TELOXIDE_TOKEN ist leer".into());
    }

    let allowed_user_id = env::var("ALLOWED_USER_ID")
        .map_err(|_| "ALLOWED_USER_ID fehlt in der lokalen .env-Datei")?
        .parse::<u64>()
        .map_err(|_| "ALLOWED_USER_ID muss eine numerische Telegram-Benutzer-ID sein")?;
    if allowed_user_id == 0 {
        return Err("ALLOWED_USER_ID darf nicht 0 sein".into());
    }

    let runtime = tokio::runtime::Runtime::new()?;
    runtime.block_on(async {
        let (tx, rx) = mpsc::channel::<AuditEvent>(100);

        // 1. Hintergrund-Wächter starten
        tokio::spawn(start_audit_watcher(tx));

        // 2. Alert-Dispatcher starten
        let alert_bot = Bot::new(token.clone());
        tokio::spawn(start_alert_dispatcher(alert_bot, allowed_user_id, rx));

        // 3. Supervisor-Loop mit automatischem Reconnect
        let mut retry_delay = Duration::from_secs(5);
        loop {
            log::info!("Connecting Telegram Dispatcher...");
            let bot = Bot::new(token.clone());

            let command_handler = Update::filter_message()
                .filter_command::<Command>()
                .endpoint(move |bot: Bot, msg: Message, cmd: Command| {
                    handle_command(bot, msg, cmd, allowed_user_id)
                });

            let callback_handler = Update::filter_callback_query()
                .endpoint(move |bot: Bot, q: CallbackQuery| {
                    handle_callback(bot, q, allowed_user_id)
                });

            let handler = dptree::entry()
                .branch(command_handler)
                .branch(callback_handler);

            let mut dispatcher = Dispatcher::builder(bot, handler).build();

            let dispatch_started = tokio::time::Instant::now();
            log::info!("Telegram Dispatcher running.");
            let dispatch = dispatcher.dispatch();
            tokio::pin!(dispatch);
            let stopped_by_signal = tokio::select! {
                _ = tokio::signal::ctrl_c() => true,
                _ = &mut dispatch => false,
            };
            if stopped_by_signal {
                log::info!("Ctrl-C empfangen; Terra Remote Engine wird kontrolliert beendet.");
                break;
            }

            let uptime = dispatch_started.elapsed();
            let delay_before_retry = retry_delay;
            if uptime >= Duration::from_secs(60) {
                retry_delay = Duration::from_secs(5);
                log::info!("Telegram-Verbindung war mindestens 60 Sekunden aktiv; Reconnect-Backoff zurückgesetzt.");
            } else {
                retry_delay = std::cmp::min(retry_delay.saturating_mul(2), Duration::from_secs(60));
            }

            log::warn!(
                "Telegram Dispatcher beendet (Laufzeit: {:?}). Neuer Verbindungsversuch in {:?}...",
                uptime,
                delay_before_retry
            );
            tokio::time::sleep(delay_before_retry).await;
        }
    });

    Ok(())
}
