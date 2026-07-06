# TUI Terminal Safety (ratatui / crossterm)

## The problem: stderr leaks outside the alternate screen

When using `ratatui::init()` (enters raw mode + alternate screen), only **stdout** is redirected to the alternate screen. **stderr** continues writing to the physical terminal. This means:

- `env_logger` (defaults to stderr) leaks log messages on top of the TUI
- `eprintln!` calls during raw mode corrupt the display
- `log::error!` / `log::info!` calls from tokio-tungstenite, crossterm, or the app itself are visible to the user

## Fixes

### 1. Log level to Warn
```rust
env_logger::Builder::new()
    .filter_level(log::LevelFilter::Warn)  // not Info
    .init();
```

### 2. Redirect to file (for production)
```rust
use std::fs::OpenOptions;
let log_file = OpenOptions::new()
    .create(true).append(true)
    .open("/tmp/chatter.log")?;
env_logger::Builder::new()
    .filter_level(log::LevelFilter::Info)
    .target(env_logger::Target::Pipe(Box::new(log_file)))
    .init();
```

### 3. Never eprintln! during raw mode
Replace with `log::error!` (which at Warn level is fine):
```rust
// bad:
eprintln!("Send error: {}", e);
// good:
log::error!("Send error: {}", e);
```

## Terminal cleanup on exit

### Signal handling: use tokio::signal, not signal_hook::flag

`signal_hook::flag::register(SIGINT, &AtomicBool)` sets a flag but does **not wake the event loop**. The app stays blocked in `channel.recv().await` with no way to read the flag.

```rust
// GOOD: tokio::signal::ctrl_c() sends an event through the channel
let signal_sender = sender.clone();
tokio::spawn(async move {
    tokio::signal::ctrl_c().await.ok();
    let _ = signal_sender.send(Event::App(AppEvent::Quit));
});

// ALSO GOOD: SIGTERM handler (Unix only)
#[cfg(unix)]
{
    use tokio::signal::unix::{signal, SignalKind};
    tokio::spawn(async move {
        if let Ok(mut stream) = signal(SignalKind::terminate()) {
            stream.recv().await;
            let _ = signal_sender.send(Event::App(AppEvent::Quit));
        }
    });
}
```

### Panic safety: TerminalGuard

Without a guard, a panic during the event loop leaves the TTY in raw mode (no echo, no line editing).

```rust
struct TerminalGuard;
impl Drop for TerminalGuard {
    fn drop(&mut self) {
        let _ = ratatui::restore();  // idempotent
    }
}

fn main() {
    let mut terminal = ratatui::init();
    terminal.clear()?;        // explicit clear for terminals that don't auto-clear
    let _guard = TerminalGuard;  // dropped on panic or normal exit
    let result = app.run(terminal).await;
    result
}
```

### terminal.clear() after init()

`ratatui::init()` enters the alternate screen but does not clear it. Some terminals show previous content in cells where the first frame is empty. Always call `terminal.clear()` after init.

```rust
let mut terminal = ratatui::init();
terminal.clear()?;  // required for portability
```

## Double restore() is safe

`ratatui::restore()` calls `crossterm::terminal::disable_raw_mode()` and `execute!(LeaveAlternateScreen)`. Both are idempotent:

- Disabling raw mode when already disabled is a no-op (returns Ok)
- Leaving alternate screen when already in the primary screen is a no-op

So calling restore() from both `TerminalGuard::drop` and `DefaultTerminal::drop` is safe.
