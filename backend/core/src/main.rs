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
fn run() -> Result<(), Box<dyn std::error::Error>> {
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
