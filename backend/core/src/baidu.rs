//! Real Baidu protocol, separate from the legacy local mock. Application secrets
//! never reach this module. Provider MD5 verification is not labelled SHA-256.
use crate::{Engine, model::*};
use fs2::FileExt;
use md5::Md5;
use reqwest::{
    blocking::{Client, multipart},
    redirect::Policy,
};
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    fs::{self, File, OpenOptions},
    io::{Read, Seek, SeekFrom, Write},
    path::{Path, PathBuf},
    time::Duration,
};

const CHUNK: usize = 4 * 1024 * 1024;
const RESPONSE_LIMIT: u64 = 2 * 1024 * 1024;

fn error(code: &str) -> CoreError {
    CoreError::new(
        code,
        match code {
            "BAIDU_NOT_AUTHORIZED" => "请先连接百度网盘。",
            "BAIDU_TOKEN_EXPIRED" => "百度授权已过期，请刷新授权后重试。",
            "BAIDU_PERMISSION_DENIED" => "百度网盘尚未授予此应用所需权限。",
            "LOCAL_HASH_MISMATCH" => "本地备份副本发生变化，已停止上传。",
            "VERIFIED_REPLICA_REQUIRED" => "只允许上传已通过 SHA-256 校验的本地备份副本。",
            "BAIDU_TASK_BUSY" => "该上传任务正在执行。",
            "BAIDU_REMOTE_HASH_MISMATCH" => "网盘返回的大小或 MD5 不一致，需要检查。",
            "BAIDU_COMMIT_UNCERTAIN" => "合并文件结果尚不明确；仅核对远端，不重复合并。",
            _ => "百度网盘请求未完成，请查看任务状态后重试。",
        },
    )
}

/// No caller-supplied host, redirect, or URL. Test transports implement the same
/// operation contract without sending any real token or file to the internet.
pub trait BaiduTransport {
    fn call(
        &self,
        operation: &str,
        token: &str,
        fields: &[(String, String)],
        part: Option<Vec<u8>>,
    ) -> CoreResult<Value>;
}

pub struct BaiduHttps {
    client: Client,
    #[cfg(test)]
    test_origin: Option<String>,
}
impl BaiduHttps {
    pub fn new() -> CoreResult<Self> {
        Ok(Self {
            client: Client::builder()
                .https_only(true)
                .redirect(Policy::none())
                .no_proxy()
                .timeout(Duration::from_secs(120))
                .user_agent("Yingxu/0.9")
                .build()
                .map_err(|_| error("BAIDU_NETWORK_UNAVAILABLE"))?,
            #[cfg(test)]
            test_origin: None,
        })
    }
    fn endpoint(&self, url: &str) -> String {
        #[cfg(test)]
        if let Some(origin) = &self.test_origin {
            return format!("{origin}{}", reqwest::Url::parse(url).unwrap().path());
        }
        url.into()
    }
}
impl BaiduTransport for BaiduHttps {
    fn call(
        &self,
        operation: &str,
        token: &str,
        fields: &[(String, String)],
        part: Option<Vec<u8>>,
    ) -> CoreResult<Value> {
        if token.is_empty() {
            return Err(error("BAIDU_NOT_AUTHORIZED"));
        }
        let result = match operation {
            "precreate" | "create" => self
                .client
                .post(self.endpoint("https://pan.baidu.com/rest/2.0/xpan/file"))
                .query(&[("method", operation), ("access_token", token)])
                .form(fields)
                .send(),
            "upload" => self
                .client
                .post(self.endpoint("https://d.pcs.baidu.com/rest/2.0/pcs/superfile2"))
                .query(&[
                    ("method", "upload"),
                    ("access_token", token),
                    ("type", "tmpfile"),
                ])
                .query(fields)
                .multipart(
                    multipart::Form::new().part(
                        "file",
                        multipart::Part::bytes(part.ok_or_else(|| error("BAIDU_INVALID_REQUEST"))?)
                            .file_name("part"),
                    ),
                )
                .send(),
            "list" => self
                .client
                .get(self.endpoint("https://pan.baidu.com/rest/2.0/xpan/file"))
                .query(&[("method", "list"), ("access_token", token)])
                .query(fields)
                .send(),
            "userinfo" => self
                .client
                .get(self.endpoint("https://pan.baidu.com/rest/2.0/xpan/nas"))
                .query(&[("method", "uinfo"), ("access_token", token)])
                .send(),
            _ => return Err(error("BAIDU_INVALID_REQUEST")),
        };
        // reqwest errors contain full URLs including tokens: never serialize them.
        let response = result.map_err(|_| error("BAIDU_NETWORK_UNAVAILABLE"))?;
        if !response.status().is_success() {
            return Err(error("BAIDU_HTTP_ERROR"));
        }
        let mut raw = Vec::new();
        response
            .take(RESPONSE_LIMIT + 1)
            .read_to_end(&mut raw)
            .map_err(|_| error("BAIDU_INVALID_RESPONSE"))?;
        if raw.len() as u64 > RESPONSE_LIMIT {
            return Err(error("BAIDU_INVALID_RESPONSE"));
        }
        let value: Value =
            serde_json::from_slice(&raw).map_err(|_| error("BAIDU_INVALID_RESPONSE"))?;
        check_response(&value)?;
        Ok(value)
    }
}

fn check_response(v: &Value) -> CoreResult<()> {
    if !v.is_object() {
        return Err(error("BAIDU_INVALID_RESPONSE"));
    }
    if let Some(n) = v.get("errno").or_else(|| v.get("error_code")) {
        match n.as_i64() {
            Some(0) => {}
            Some(-6 | 110 | 111) => return Err(error("BAIDU_TOKEN_EXPIRED")),
            Some(6 | 31024 | 31045) => return Err(error("BAIDU_PERMISSION_DENIED")),
            _ => return Err(error("BAIDU_PROVIDER_ERROR")),
        }
    } else if v.get("md5").is_none() {
        return Err(error("BAIDU_INVALID_RESPONSE"));
    }
    Ok(())
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct BaiduTask {
    pub task_id: String,
    pub owner_id: String,
    pub replica_id: String,
    pub source: PathBuf,
    pub remote_path: String,
    pub bytes: u64,
    pub sha256: String,
    pub md5: String,
    pub blocks: Vec<String>,
    pub upload_id: Option<String>,
    pub chunks_done: Vec<usize>,
    pub state: String,
    pub error: Option<String>,
    pub remote_id: Option<u64>,
    pub verification: Option<String>,
    pub updated_at: String,
    #[serde(default)]
    pub speed_bytes_per_second: u64,
}

impl BaiduTask {
    pub fn summary(&self) -> Value {
        let uploaded: u64 = self
            .chunks_done
            .iter()
            .map(|index| {
                self.bytes
                    .saturating_sub(*index as u64 * CHUNK as u64)
                    .min(CHUNK as u64)
            })
            .sum();
        let speed = if self.state == "uploading" {
            self.speed_bytes_per_second
        } else {
            0
        };
        json!({"taskId":self.task_id,"replicaId":self.replica_id,
        "localPath":self.source,"remotePath":self.remote_path,"bytes":self.bytes,"sha256":self.sha256,
        "state":self.state,"error":self.error,"chunksDone":self.chunks_done.len(),"chunksTotal":self.blocks.len(),
        "verifiedRemote":self.verification.is_some(),"verification":self.verification,"updatedAt":self.updated_at,
        "uploadedBytes":uploaded,"speedBytesPerSecond":speed,"remainingSeconds":if speed>0{Some(self.bytes.saturating_sub(uploaded).div_ceil(speed))}else{None}})
    }
}

fn task_path(root: &Path, task_id: &str) -> CoreResult<PathBuf> {
    if uuid::Uuid::parse_str(task_id).is_err() {
        return Err(error("BAIDU_INVALID_TASK"));
    }
    Ok(root
        .join("baidu")
        .join("tasks")
        .join(format!("{task_id}.json")))
}

fn save(root: &Path, task: &BaiduTask) -> CoreResult<()> {
    let path = task_path(root, &task.task_id)?;
    fs::create_dir_all(path.parent().unwrap())?;
    let temporary = path.with_extension("json.tmp");
    let mut file = File::create(&temporary)?;
    file.write_all(&serde_json::to_vec_pretty(task)?)?;
    file.sync_all()?;
    fs::rename(temporary, path)?;
    Ok(())
}

fn load(root: &Path, owner: &str, task_id: &str) -> CoreResult<BaiduTask> {
    let task: BaiduTask = serde_json::from_slice(&fs::read(task_path(root, task_id)?)?)?;
    if task.owner_id != owner {
        return Err(error("BAIDU_TASK_NOT_FOUND"));
    }
    Ok(task)
}

fn hashes(source: &Path) -> CoreResult<(u64, String, String, Vec<String>)> {
    let mut file = File::open(source)?;
    if !file.metadata()?.is_file() {
        return Err(error("VERIFIED_REPLICA_REQUIRED"));
    }
    let mut whole_sha = Sha256::new();
    let mut whole_md5 = Md5::new();
    let mut bytes = 0;
    let mut blocks = vec![];
    loop {
        let mut buf = vec![0; CHUNK];
        let mut len = 0;
        while len < CHUNK {
            let n = file.read(&mut buf[len..])?;
            if n == 0 {
                break;
            }
            len += n;
        }
        if len == 0 {
            break;
        }
        buf.truncate(len);
        bytes += len as u64;
        whole_sha.update(&buf);
        whole_md5.update(&buf);
        blocks.push(format!("{:x}", Md5::digest(&buf)));
    }
    if blocks.is_empty() {
        blocks.push(format!("{:x}", Md5::digest([])));
    }
    Ok((
        bytes,
        format!("{:x}", whole_sha.finalize()),
        format!("{:x}", whole_md5.finalize()),
        blocks,
    ))
}

fn remote_path(folder: &str, name: &str, task_id: &str) -> CoreResult<String> {
    if !folder.starts_with("/apps/")
        || folder.split('/').count() != 3
        || folder.ends_with('/')
        || folder.contains(['\\', '?', '#'])
        || folder.chars().any(char::is_control)
        || matches!(folder.rsplit('/').next(), Some("." | ".."))
    {
        return Err(error("BAIDU_INVALID_APP_FOLDER"));
    }
    crate::services::safe_component(name)?;
    // Stable unique path also allows read-only reconciliation after a lost create
    // response. rtype=0 never overwrites or silently invents a second filename.
    let path = Path::new(name);
    let stem = path.file_stem().and_then(|s| s.to_str()).unwrap_or("media");
    let extension = path
        .extension()
        .and_then(|s| s.to_str())
        .map(|s| format!(".{s}"))
        .unwrap_or_default();
    Ok(format!("{folder}/{stem}_{task_id}{extension}"))
}

impl Engine {
    pub fn baidu_enqueue(
        &self,
        owner: &str,
        replica_id: &str,
        task_id: &str,
        folder: &str,
    ) -> CoreResult<Value> {
        let path = task_path(&self.store.root, task_id)?;
        fs::create_dir_all(path.parent().unwrap())?;
        let lock = OpenOptions::new()
            .create(true)
            .truncate(false)
            .write(true)
            .open(path.with_extension("lock"))?;
        FileExt::try_lock_exclusive(&lock).map_err(|_| error("BAIDU_TASK_BUSY"))?;
        if path.exists() {
            let task = load(&self.store.root, owner, task_id)?;
            if task.replica_id != replica_id || !task.remote_path.starts_with(&format!("{folder}/"))
            {
                return Err(error("BAIDU_TASK_CONFLICT"));
            }
            return Ok(task.summary());
        }
        let (source, expected): (String, String) = self
            .store
            .connect()?
            .query_row(
                "SELECT path,sha256 FROM replicas WHERE id=?1 AND verified=1",
                [replica_id],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .map_err(|_| error("VERIFIED_REPLICA_REQUIRED"))?;
        let source = PathBuf::from(source).canonicalize()?;
        let (bytes, sha256, md5, blocks) = hashes(&source)?;
        if expected != sha256 {
            return Err(error("LOCAL_HASH_MISMATCH"));
        }
        let name = source
            .file_name()
            .and_then(|s| s.to_str())
            .ok_or_else(|| error("BAIDU_INVALID_FILENAME"))?;
        let remote_path = remote_path(folder, name, task_id)?;
        let task = BaiduTask {
            task_id: task_id.into(),
            owner_id: owner.into(),
            replica_id: replica_id.into(),
            source,
            remote_path,
            bytes,
            sha256,
            md5,
            blocks,
            upload_id: None,
            chunks_done: vec![],
            state: "queued".into(),
            error: None,
            remote_id: None,
            verification: None,
            updated_at: now(),
            speed_bytes_per_second: 0,
        };
        save(&self.store.root, &task)?;
        Ok(task.summary())
    }

    pub fn baidu_tasks(&self, owner: &str) -> CoreResult<Value> {
        let mut tasks = vec![];
        let dir = self.store.root.join("baidu/tasks");
        if dir.exists() {
            for entry in fs::read_dir(dir)? {
                let path = entry?.path();
                if path.extension().and_then(|s| s.to_str()) != Some("json") {
                    continue;
                }
                if let Some(task) = fs::read(path)
                    .ok()
                    .and_then(|raw| serde_json::from_slice::<BaiduTask>(&raw).ok())
                    .filter(|task| task.owner_id == owner)
                {
                    tasks.push(task.summary());
                }
            }
        }
        tasks.sort_by(|a, b| b["updatedAt"].as_str().cmp(&a["updatedAt"].as_str()));
        Ok(json!(tasks))
    }

    pub fn baidu_cancel(&self, owner: &str, task_id: &str) -> CoreResult<Value> {
        let task = load(&self.store.root, owner, task_id)?;
        if task.state == "completed" {
            return Err(error("BAIDU_ALREADY_COMPLETED"));
        }
        fs::write(
            task_path(&self.store.root, task_id)?.with_extension("cancel"),
            b"cancel",
        )?;
        Ok(json!({"taskId":task_id,"state":"cancelling","remoteDeleted":false}))
    }

    pub fn baidu_run(
        &self,
        owner: &str,
        task_id: &str,
        token: &str,
        transport: &dyn BaiduTransport,
    ) -> CoreResult<Value> {
        let path = task_path(&self.store.root, task_id)?;
        let lock = OpenOptions::new()
            .create(true)
            .truncate(false)
            .write(true)
            .open(path.with_extension("lock"))?;
        FileExt::try_lock_exclusive(&lock).map_err(|_| error("BAIDU_TASK_BUSY"))?;
        let mut task = load(&self.store.root, owner, task_id)?;
        if task.state == "completed" {
            return Ok(task.summary());
        }
        if path.with_extension("cancel").exists()
            && !matches!(task.state.as_str(), "committing" | "commit_uncertain")
        {
            task.state = "cancelled".into();
            save(&self.store.root, &task)?;
            return Ok(task.summary());
        }
        let outcome = self.baidu_run_inner(&mut task, token, transport);
        if let Err(failure) = outcome {
            task.error = Some(failure.code.clone());
            if task.state == "committing" {
                task.state = "commit_uncertain".into();
            } else if task.state != "commit_uncertain" {
                task.state = "failed".into();
            }
        }
        task.updated_at = now();
        save(&self.store.root, &task)?;
        Ok(task.summary())
    }

    fn baidu_run_inner(
        &self,
        task: &mut BaiduTask,
        token: &str,
        transport: &dyn BaiduTransport,
    ) -> CoreResult<()> {
        if matches!(task.state.as_str(), "committing" | "commit_uncertain") {
            let (folder, _) = task.remote_path.rsplit_once('/').unwrap();
            for start in (0..100_000).step_by(1000) {
                let value = transport.call(
                    "list",
                    token,
                    &[
                        ("dir".into(), folder.into()),
                        ("start".into(), start.to_string()),
                        ("limit".into(), "1000".into()),
                    ],
                    None,
                )?;
                let files = value["list"]
                    .as_array()
                    .ok_or_else(|| error("BAIDU_INVALID_RESPONSE"))?;
                if let Some(file) = files.iter().find(|f| f["path"] == task.remote_path) {
                    return verify(task, file);
                }
                if files.len() < 1000 {
                    break;
                }
            }
            return Err(error("BAIDU_COMMIT_UNCERTAIN"));
        }
        let (size, sha, _, blocks) = hashes(&task.source)?;
        if sha != task.sha256 || size != task.bytes || blocks != task.blocks {
            return Err(error("LOCAL_HASH_MISMATCH"));
        }
        let mut fields = vec![
            ("path".into(), task.remote_path.clone()),
            ("size".into(), task.bytes.to_string()),
            ("isdir".into(), "0".into()),
            ("rtype".into(), "0".into()),
            ("block_list".into(), serde_json::to_string(&task.blocks)?),
        ];
        if task.upload_id.is_none() {
            let mut pre = fields.clone();
            pre.push(("autoinit".into(), "1".into()));
            let value = transport.call("precreate", token, &pre, None)?;
            if value["return_type"] == 2 {
                task.state = "commit_uncertain".into();
                save(&self.store.root, task)?;
                return Err(error("BAIDU_COMMIT_UNCERTAIN"));
            }
            if value["return_type"] != 1 {
                return Err(error("BAIDU_INVALID_RESPONSE"));
            }
            task.upload_id = Some(
                value["uploadid"]
                    .as_str()
                    .filter(|s| !s.is_empty() && s.len() < 4096)
                    .ok_or_else(|| error("BAIDU_INVALID_RESPONSE"))?
                    .into(),
            );
            let missing = value["block_list"]
                .as_array()
                .ok_or_else(|| error("BAIDU_INVALID_RESPONSE"))?;
            let missing = missing
                .iter()
                .map(|v| {
                    v.as_u64()
                        .filter(|n| *n < task.blocks.len() as u64)
                        .map(|n| n as usize)
                        .ok_or_else(|| error("BAIDU_INVALID_RESPONSE"))
                })
                .collect::<CoreResult<Vec<_>>>()?;
            task.chunks_done = (0..task.blocks.len())
                .filter(|index| !missing.contains(index))
                .collect();
            task.state = "uploading".into();
            save(&self.store.root, task)?;
        }
        let mut file = File::open(&task.source)?;
        for index in 0..task.blocks.len() {
            if task.chunks_done.contains(&index) {
                continue;
            }
            if task_path(&self.store.root, &task.task_id)?
                .with_extension("cancel")
                .exists()
            {
                task.state = "cancelled".into();
                return Ok(());
            }
            file.seek(SeekFrom::Start(index as u64 * CHUNK as u64))?;
            let mut bytes = vec![
                0;
                task.bytes
                    .saturating_sub(index as u64 * CHUNK as u64)
                    .min(CHUNK as u64) as usize
            ];
            file.read_exact(&mut bytes)?;
            if format!("{:x}", Md5::digest(&bytes)) != task.blocks[index] {
                return Err(error("LOCAL_HASH_MISMATCH"));
            }
            let fields = vec![
                ("path".into(), task.remote_path.clone()),
                ("uploadid".into(), task.upload_id.clone().unwrap()),
                ("partseq".into(), index.to_string()),
            ];
            let part_size = bytes.len() as f64;
            let part_started = std::time::Instant::now();
            let value = transport.call("upload", token, &fields, Some(bytes))?;
            if !md5_matches(value["md5"].as_str(), &task.blocks[index]) {
                return Err(error("BAIDU_REMOTE_HASH_MISMATCH"));
            }
            task.chunks_done.push(index);
            task.speed_bytes_per_second =
                (part_size / part_started.elapsed().as_secs_f64().max(0.001)) as u64;
            task.state = "uploading".into();
            task.updated_at = now();
            save(&self.store.root, task)?;
        }
        let (size, sha, _, _) = hashes(&task.source)?;
        if size != task.bytes || sha != task.sha256 {
            return Err(error("LOCAL_HASH_MISMATCH"));
        }
        if task_path(&self.store.root, &task.task_id)?
            .with_extension("cancel")
            .exists()
        {
            task.state = "cancelled".into();
            return Ok(());
        }
        fields.push(("uploadid".into(), task.upload_id.clone().unwrap()));
        task.state = "committing".into();
        save(&self.store.root, task)?;
        let value = transport.call("create", token, &fields, None)?;
        verify(task, &value)
    }
}

fn verify(task: &mut BaiduTask, value: &Value) -> CoreResult<()> {
    task.remote_id = value["fs_id"].as_u64();
    if task.remote_id.is_none()
        || value["path"] != task.remote_path
        || value["size"].as_u64() != Some(task.bytes)
        || !md5_matches(value["md5"].as_str(), &task.md5)
    {
        return Err(error("BAIDU_REMOTE_HASH_MISMATCH"));
    }
    task.state = "completed".into();
    task.error = None;
    task.verification = Some("provider_md5_and_size".into());
    Ok(())
}

fn md5_matches(actual: Option<&str>, expected: &str) -> bool {
    actual.is_some_and(|value| value.eq_ignore_ascii_case(expected))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::{Cell, RefCell};

    struct Mock {
        fail_part: Cell<bool>,
        lose_create: Cell<bool>,
        bad_md5: Cell<bool>,
        uppercase_md5: Cell<bool>,
        uploads: RefCell<Vec<usize>>,
        creates: Cell<usize>,
        value: RefCell<Value>,
    }
    impl Mock {
        fn new() -> Self {
            Self {
                fail_part: Cell::new(false),
                lose_create: Cell::new(false),
                bad_md5: Cell::new(false),
                uppercase_md5: Cell::new(false),
                uploads: RefCell::new(vec![]),
                creates: Cell::new(0),
                value: RefCell::new(Value::Null),
            }
        }
    }
    impl BaiduTransport for Mock {
        fn call(
            &self,
            op: &str,
            _: &str,
            fields: &[(String, String)],
            part: Option<Vec<u8>>,
        ) -> CoreResult<Value> {
            let get = |name: &str| fields.iter().find(|p| p.0 == name).unwrap().1.clone();
            match op {
                "precreate" => {
                    assert_eq!(get("rtype"), "0");
                    assert_eq!(get("autoinit"), "1");
                    let blocks: Vec<String> = serde_json::from_str(&get("block_list")).unwrap();
                    Ok(
                        json!({"errno":0,"return_type":1,"uploadid":"synthetic-upload","block_list":(0..blocks.len()).collect::<Vec<_>>()}),
                    )
                }
                "upload" => {
                    let index: usize = get("partseq").parse().unwrap();
                    self.uploads.borrow_mut().push(index);
                    if index == 1 && self.fail_part.replace(false) {
                        return Err(error("BAIDU_NETWORK_UNAVAILABLE"));
                    }
                    let data = part.unwrap();
                    assert!(data.len() <= CHUNK);
                    let md5 = if self.bad_md5.get() {
                        "wrong".into()
                    } else {
                        let value = format!("{:x}", Md5::digest(data));
                        if self.uppercase_md5.get() {
                            value.to_ascii_uppercase()
                        } else {
                            value
                        }
                    };
                    Ok(json!({"md5":md5}))
                }
                "create" => {
                    self.creates.set(self.creates.get() + 1);
                    assert_eq!(get("rtype"), "0");
                    if self.lose_create.get() {
                        Err(error("BAIDU_NETWORK_UNAVAILABLE"))
                    } else {
                        Ok(self.value.borrow().clone())
                    }
                }
                "list" => Ok(json!({"errno":0,"list":[self.value.borrow().clone()]})),
                _ => panic!("unexpected operation"),
            }
        }
    }
    fn fixture(data: &[u8]) -> (tempfile::TempDir, Engine, String, String) {
        let dir = tempfile::tempdir().unwrap();
        let engine = Engine::open(dir.path().join("db")).unwrap();
        let source = dir.path().join("SYNTHETIC.mp4");
        fs::write(&source, data).unwrap();
        let (_, sha, _, _) = hashes(&source).unwrap();
        let replica = id();
        let task = id();
        engine
            .store
            .connect()
            .unwrap()
            .execute(
                "INSERT INTO replicas VALUES(?1,'synthetic-job','synthetic-media',?2,?3,1,'now')",
                rusqlite::params![replica, source.to_str().unwrap(), sha],
            )
            .unwrap();
        (dir, engine, replica, task)
    }
    fn prepare(engine: &Engine, replica: &str, task_id: &str, mock: &Mock) {
        engine
            .baidu_enqueue("owner", replica, task_id, "/apps/影序测试")
            .unwrap();
        let task = load(&engine.store.root, "owner", task_id).unwrap();
        *mock.value.borrow_mut() =
            json!({"errno":0,"path":task.remote_path,"size":task.bytes,"md5":task.md5,"fs_id":42});
    }
    #[test]
    fn resumable_chunks_keep_local_sha_and_validate_provider_md5() {
        let (_dir, e, replica, task) = fixture(&vec![42; CHUNK + 3]);
        let mock = Mock::new();
        mock.fail_part.set(true);
        prepare(&e, &replica, &task, &mock);
        let first = e
            .baidu_run("owner", &task, "synthetic-token", &mock)
            .unwrap();
        assert_eq!(first["state"], "failed");
        assert_eq!(first["chunksDone"], 1);
        let second = e
            .baidu_run("owner", &task, "synthetic-token", &mock)
            .unwrap();
        assert_eq!(second["state"], "completed");
        assert_eq!(second["verification"], "provider_md5_and_size");
        assert_eq!(*mock.uploads.borrow(), [0, 1, 1]);
        e.baidu_run("owner", &task, "synthetic-token", &mock)
            .unwrap();
        assert_eq!(mock.creates.get(), 1);
        let raw = fs::read_to_string(task_path(&e.store.root, &task).unwrap()).unwrap();
        assert!(!raw.contains("synthetic-token"));
    }
    #[test]
    fn md5_comparison_accepts_hex_case_variants() {
        let (_dir, e, replica, task) = fixture(b"synthetic");
        let mock = Mock::new();
        mock.uppercase_md5.set(true);
        prepare(&e, &replica, &task, &mock);
        let value = mock.value.borrow().clone();
        mock.value.borrow_mut()["md5"] = value["md5"].as_str().unwrap().to_ascii_uppercase().into();
        let result = e
            .baidu_run("owner", &task, "synthetic-token", &mock)
            .unwrap();
        assert_eq!(result["state"], "completed");
        assert_eq!(result["verification"], "provider_md5_and_size");
    }
    #[test]
    fn refuse_unverified_or_changed_replica_before_any_network() {
        let (_dir, e, replica, task) = fixture(b"synthetic");
        let mock = Mock::new();
        prepare(&e, &replica, &task, &mock);
        assert!(
            e.baidu_enqueue("owner", "missing", &id(), "/apps/test")
                .is_err()
        );
        let t = load(&e.store.root, "owner", &task).unwrap();
        fs::write(t.source, b"modified").unwrap();
        assert_eq!(
            e.baidu_run("owner", &task, "token", &mock).unwrap()["error"],
            "LOCAL_HASH_MISMATCH"
        );
        assert!(mock.uploads.borrow().is_empty());
        assert_eq!(mock.creates.get(), 0);
    }
    #[test]
    fn lost_create_response_reconciles_without_second_commit() {
        let (_dir, e, replica, task) = fixture(b"synthetic");
        let mock = Mock::new();
        mock.lose_create.set(true);
        prepare(&e, &replica, &task, &mock);
        assert_eq!(
            e.baidu_run("owner", &task, "token", &mock).unwrap()["state"],
            "commit_uncertain"
        );
        assert_eq!(
            e.baidu_run("owner", &task, "token", &mock).unwrap()["state"],
            "completed"
        );
        assert_eq!(mock.creates.get(), 1);
    }
    #[test]
    fn bad_part_hash_never_commits_and_cancel_never_uploads() {
        let (_dir, e, replica, task) = fixture(b"synthetic");
        let mock = Mock::new();
        mock.bad_md5.set(true);
        prepare(&e, &replica, &task, &mock);
        let result = e.baidu_run("owner", &task, "token", &mock).unwrap();
        assert_eq!(result["chunksDone"], 0);
        assert_eq!(result["error"], "BAIDU_REMOTE_HASH_MISMATCH");
        assert_eq!(mock.creates.get(), 0);
        e.baidu_cancel("owner", &task).unwrap();
        assert_eq!(
            e.baidu_run("owner", &task, "token", &mock).unwrap()["state"],
            "cancelled"
        );
        assert_eq!(mock.uploads.borrow().len(), 1);
    }
    #[test]
    fn owner_isolation_idempotency_conflict_and_task_lock() {
        let (_dir, e, replica, task) = fixture(b"synthetic");
        let mock = Mock::new();
        prepare(&e, &replica, &task, &mock);
        assert_eq!(e.baidu_tasks("other").unwrap(), json!([]));
        assert!(e.baidu_run("other", &task, "token", &mock).is_err());
        assert!(
            e.baidu_enqueue("owner", "other-replica", &task, "/apps/影序测试")
                .is_err()
        );
        assert_eq!(
            e.baidu_enqueue("owner", &replica, &task, "/apps/影序测试")
                .unwrap()["taskId"],
            task
        );
        let lock = File::create(
            task_path(&e.store.root, &task)
                .unwrap()
                .with_extension("lock"),
        )
        .unwrap();
        FileExt::lock_exclusive(&lock).unwrap();
        assert!(e.baidu_run("owner", &task, "token", &mock).is_err());
    }
    #[test]
    fn remote_paths_never_escape_application_folder() {
        assert!(remote_path("/apps/..", "photo.jpg", &id()).is_err());
        assert!(remote_path("/apps/test", "../photo.jpg", &id()).is_err());
        assert!(remote_path("/other/test", "photo.jpg", &id()).is_err());
    }
    #[test]
    fn actual_http_encoding_multipart_and_redacted_errors() {
        use std::net::TcpListener;
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let origin = format!("http://{}", listener.local_addr().unwrap());
        let server = std::thread::spawn(move || {
            let mut requests = vec![];
            for body in [
                "{\"errno\":0,\"return_type\":1,\"uploadid\":\"id\",\"block_list\":[0]}",
                "{\"md5\":\"900150983cd24fb0d6963f7d28e17f72\"}",
                "{\"errno\":-6,\"error_msg\":\"sensitive-do-not-reflect\"}",
            ] {
                let (mut socket, _) = listener.accept().unwrap();
                socket
                    .set_read_timeout(Some(Duration::from_secs(3)))
                    .unwrap();
                let mut bytes = vec![];
                let mut buf = [0; 8192];
                loop {
                    let n = socket.read(&mut buf).unwrap();
                    if n == 0 {
                        break;
                    }
                    bytes.extend_from_slice(&buf[..n]);
                    if let Some(split) = bytes.windows(4).position(|p| p == b"\r\n\r\n") {
                        let head = String::from_utf8_lossy(&bytes[..split]).to_lowercase();
                        let length = head
                            .lines()
                            .find_map(|l| {
                                l.strip_prefix("content-length:")
                                    .and_then(|s| s.trim().parse::<usize>().ok())
                            })
                            .unwrap_or(0);
                        if bytes.len() >= split + 4 + length {
                            break;
                        }
                    }
                }
                requests.push(String::from_utf8_lossy(&bytes).to_string());
                write!(
                    socket,
                    "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                    body.len()
                )
                .unwrap();
            }
            requests
        });
        let client = BaiduHttps {
            client: Client::builder()
                .no_proxy()
                .redirect(Policy::none())
                .build()
                .unwrap(),
            test_origin: Some(origin),
        };
        client
            .call(
                "precreate",
                "synthetic-token",
                &[
                    ("path".into(), "/apps/测试/a b.jpg".into()),
                    ("block_list".into(), "[\"hash\"]".into()),
                ],
                None,
            )
            .unwrap();
        client
            .call(
                "upload",
                "synthetic-token",
                &[("partseq".into(), "0".into())],
                Some(b"abc".to_vec()),
            )
            .unwrap();
        let failure = client
            .call("userinfo", "synthetic-token", &[], None)
            .unwrap_err();
        assert_eq!(failure.code, "BAIDU_TOKEN_EXPIRED");
        assert!(!failure.message.contains("sensitive"));
        assert!(!failure.message.contains("synthetic-token"));
        let requests = server.join().unwrap();
        assert!(
            requests[0].starts_with(
                "POST /rest/2.0/xpan/file?method=precreate&access_token=synthetic-token"
            )
        );
        assert!(requests[0].contains("path=%2Fapps%2F%E6%B5%8B%E8%AF%95%2Fa+b.jpg"));
        assert!(requests[1].contains("multipart/form-data; boundary="));
        assert!(requests[1].contains("name=\"file\""));
        assert!(requests[1].contains("\r\n\r\nabc\r\n"));
    }
}
