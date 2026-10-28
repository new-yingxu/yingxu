//! Local JSON-lines transport for verification. No HTTP listener or remote shell.
use std::{
    io::{self, BufRead, Write},
    path::PathBuf,
};
use yingxu_core::{
    CoreError, Engine,
    api::{PROTOCOL_VERSION, Reply, Request},
};

fn main() {
    if let Err(e) = run() {
        eprintln!("{e}");
        std::process::exit(1)
    }
}
/// 装载 `.env`（键=值，# 注释）。只设当前进程环境，已存在的变量不覆盖。
/// 查找顺序：$YINGXU_ENV → ./.env → ./backend/.env（后两个供开发联调）。
fn load_dotenv() {
    let candidates = [
        std::env::var("YINGXU_ENV").ok().map(PathBuf::from),
        Some(PathBuf::from(".env")),
        Some(PathBuf::from("backend/.env")),
    ];
    for path in candidates.into_iter().flatten() {
        let Ok(raw) = std::fs::read_to_string(&path) else {
            continue;
        };
        for line in raw.lines() {
            let line = line.trim();
            if line.is_empty() || line.starts_with('#') {
                continue;
            }
            let Some((k, v)) = line.split_once('=') else {
                continue;
            };
            let (k, v) = (k.trim(), v.trim());
            if k.is_empty() || std::env::var_os(k).is_some() {
                continue;
            }
            // 单线程启动阶段调用，无并发风险
            #[allow(unsafe_op_in_unsafe_fn)]
            unsafe {
                std::env::set_var(k, v);
            }
        }
        break;
    }
}

fn run() -> Result<(), Box<dyn std::error::Error>> {
    load_dotenv();
    let mut args = std::env::args().skip(1);
    if args.next().as_deref() != Some("--data-dir") {
        return Err(
            "Usage: yingxu-core --data-dir <explicit-directory> [rpc|worker-once|worker-drain]"
                .into(),
        );
    }
    let root = PathBuf::from(args.next().ok_or("Missing data directory")?);
    let mode = args.next().unwrap_or_else(|| "rpc".into());
    if args.next().is_some() {
        return Err("Unexpected arguments".into());
    }
    let engine = Engine::open(root)?;
    match mode.as_str() {
        "rpc" => {
            let stdin = io::stdin();
            let mut output = io::BufWriter::new(io::stdout().lock());
            for line in stdin.lock().lines() {
                let line = line?;
                if line.trim().is_empty() {
                    continue;
                }
                let reply = if line.len() > 1024 * 1024 {
                    Reply {
                        request_id: String::new(),
                        version: PROTOCOL_VERSION,
                        ok: false,
                        data: None,
                        error: Some(CoreError::new("REQUEST_TOO_LARGE", "请求不得超过 1 MiB")),
                    }
                } else {
                    match serde_json::from_str::<Request>(&line) {
                        Ok(request) => engine.dispatch(request),
                        Err(e) => Reply {
                            request_id: String::new(),
                            version: PROTOCOL_VERSION,
                            ok: false,
                            data: None,
                            error: Some(CoreError::new("INVALID_REQUEST", e)),
                        },
                    }
                };
                serde_json::to_writer(&mut output, &reply)?;
                writeln!(output)?;
                output.flush()?;
            }
        }
        "worker-once" => {
            let job = engine.worker()?.run_next()?;
            println!("{}", serde_json::to_string(&job)?);
        }
        "worker-drain" => {
            let worker = engine.worker()?;
            while let Some(job) = worker.run_next()? {
                println!("{}", serde_json::to_string(&job)?);
            }
        }
        _ => return Err("Unknown mode".into()),
    }
    Ok(())
}
