use crate::model::*;
use rusqlite::{Connection, params};
use std::{
    path::{Path, PathBuf},
    time::Duration,
};

#[derive(Clone)]
pub struct Store {
    pub root: PathBuf,
}
impl Store {
    pub fn open(root: impl AsRef<Path>) -> CoreResult<Self> {
        std::fs::create_dir_all(root.as_ref())?;
        let this = Self {
            root: root.as_ref().canonicalize()?,
        };
        let mut c = this.connect()?;
        let version: i32 = c.query_row("PRAGMA user_version", [], |r| r.get(0))?;
        if version > 3 {
            return Err(CoreError::new(
                "DATABASE_VERSION_NEWER",
                "数据库版本高于当前程序，未修改数据库",
            ));
        }
        if version == 0 {
            let tx = c.transaction()?;
            tx.execute_batch(include_str!("schema.sql"))?;
            tx.pragma_update(None, "user_version", 1)?;
            tx.commit()?;
        }
        if version <= 1 {
            let tx = c.transaction()?;
            tx.execute_batch(
                "CREATE TABLE IF NOT EXISTS quarantine_items(\
                    id TEXT PRIMARY KEY, media_id TEXT NOT NULL, name TEXT NOT NULL,\
                    bytes INTEGER NOT NULL, sha256 TEXT NOT NULL, quarantine_path TEXT NOT NULL,\
                    state TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL\
                );\
                CREATE INDEX IF NOT EXISTS quarantine_state_created ON quarantine_items(state, created_at);\
                CREATE TABLE IF NOT EXISTS quarantine_actions(\
                    id TEXT PRIMARY KEY, quarantine_id TEXT NOT NULL REFERENCES quarantine_items(id),\
                    expires_at TEXT NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL\
                );\
                PRAGMA user_version=2;",
            )?;
            tx.commit()?;
        }
        if version <= 2 {
            let tx = c.transaction()?;
            tx.execute_batch(
                "CREATE TABLE IF NOT EXISTS file_action_previews(\
                    id TEXT PRIMARY KEY, tool TEXT NOT NULL, media_id TEXT NOT NULL,\
                    source_path TEXT NOT NULL, destination_path TEXT NOT NULL,\
                    source_fingerprint TEXT NOT NULL, source_sha256 TEXT NOT NULL,\
                    expires_at_ms INTEGER NOT NULL, state TEXT NOT NULL,\
                    created_at TEXT NOT NULL, receipt TEXT\
                );\
                CREATE INDEX IF NOT EXISTS file_action_state_expiry ON file_action_previews(state,expires_at_ms);\
                PRAGMA user_version=3;",
            )?;
            tx.commit()?;
        }
        let check: String = c.query_row("PRAGMA quick_check", [], |r| r.get(0))?;
        if check != "ok" {
            return Err(CoreError::new("DATABASE_CORRUPT", check));
        }
        Ok(this)
    }
    pub fn connect(&self) -> CoreResult<Connection> {
        let c = Connection::open(self.root.join("yingxu.sqlite"))?;
        c.busy_timeout(Duration::from_secs(5))?;
        c.execute_batch(
            "PRAGMA journal_mode=WAL; PRAGMA foreign_keys=ON; PRAGMA synchronous=FULL;",
        )?;
        Ok(c)
    }
    pub fn put<T: serde::Serialize>(&self, table: &str, key: &str, value: &T) -> CoreResult<()> {
        if !["plans", "settings", "presets", "removals"].contains(&table) {
            return Err(CoreError::new("INVALID_TABLE", table));
        }
        self.connect()?.execute(&format!("INSERT INTO {table}(id,data) VALUES(?1,?2) ON CONFLICT(id) DO UPDATE SET data=excluded.data"),params![key,serde_json::to_string(value)?])?;
        Ok(())
    }
    pub fn get<T: serde::de::DeserializeOwned>(&self, table: &str, key: &str) -> CoreResult<T> {
        if !["plans", "settings", "presets", "removals"].contains(&table) {
            return Err(CoreError::new("INVALID_TABLE", table));
        }
        let s: String = self
            .connect()?
            .query_row(
                &format!("SELECT data FROM {table} WHERE id=?1"),
                [key],
                |r| r.get(0),
            )
            .map_err(|e| {
                if matches!(e, rusqlite::Error::QueryReturnedNoRows) {
                    CoreError::new("NOT_FOUND", key)
                } else {
                    e.into()
                }
            })?;
        Ok(serde_json::from_str(&s)?)
    }
    pub fn job(&self, key: &str) -> CoreResult<Job> {
        let c = self.connect()?;
        let data: String = c
            .query_row("SELECT data FROM jobs WHERE id=?1", [key], |r| r.get(0))
            .map_err(|e| {
                if matches!(e, rusqlite::Error::QueryReturnedNoRows) {
                    CoreError::new("NOT_FOUND", key)
                } else {
                    e.into()
                }
            })?;
        let mut job: Job = serde_json::from_str(&data)?;
        let (state, phase, runtime): (String, String, String) = c.query_row(
            "SELECT state,phase,runtime FROM jobs WHERE id=?1",
            [key],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
        )?;
        job.state = state;
        job.phase = phase;
        job.runtime = serde_json::from_str(&runtime)?;
        let mut st = c.prepare("SELECT data FROM job_items WHERE job_id=?1 ORDER BY id")?;
        let rows = st.query_map([key], |r| r.get::<_, String>(0))?;
        job.items = rows
            .map(|r| Ok(serde_json::from_str(&r?)?))
            .collect::<CoreResult<Vec<_>>>()?;
        for target in &mut job.targets {
            let items: Vec<_> = job
                .items
                .iter()
                .filter(|i| i.target_id == target.id)
                .collect();
            target.copied = items.iter().filter(|i| i.state == "copied").count();
            target.skipped = items.iter().filter(|i| i.state == "skipped").count();
            target.verified = items.iter().filter(|i| i.verified).count();
            target.failed = items.iter().filter(|i| i.state == "failed").count();
            target.state = if target.failed > 0 {
                if target.copied + target.skipped > 0 {
                    "partial"
                } else {
                    "failed"
                }
            } else if target.copied + target.skipped == items.len() {
                "success"
            } else {
                &job.state
            }
            .into();
        }
        let completed: u64 = job
            .items
            .iter()
            .filter(|i| ["copied", "skipped", "failed"].contains(&i.state.as_str()))
            .map(|i| i.fingerprint.bytes)
            .sum();
        let current = job
            .items
            .iter()
            .find(|i| i.state == "pending" && Some(i.id.as_str()) == job.runtime["itemId"].as_str())
            .map_or(0, |i| {
                job.runtime["currentBytes"]
                    .as_u64()
                    .unwrap_or(0)
                    .min(i.fingerprint.bytes)
            });
        let total = job.total_bytes.saturating_mul(job.targets.len() as u64);
        job.progress = if total == 0 {
            if job
                .items
                .iter()
                .all(|i| ["copied", "skipped"].contains(&i.state.as_str()))
            {
                100.0
            } else {
                0.0
            }
        } else {
            100.0 * (completed + current) as f64 / total as f64
        };
        Ok(job)
    }
    pub fn jobs(&self) -> CoreResult<Vec<Job>> {
        let c = self.connect()?;
        let mut s = c.prepare("SELECT id FROM jobs ORDER BY created_at DESC,id")?;
        let ids = s
            .query_map([], |r| r.get::<_, String>(0))?
            .collect::<Result<Vec<_>, _>>()?;
        ids.iter().map(|id| self.job(id)).collect()
    }
    pub fn state(&self, id: &str) -> CoreResult<String> {
        Ok(self
            .connect()?
            .query_row("SELECT state FROM jobs WHERE id=?1", [id], |r| r.get(0))?)
    }
    pub fn audit(&self, action: &str, object: &str, data: serde_json::Value) -> CoreResult<()> {
        self.connect()?.execute(
            "INSERT INTO audit_events(id,created_at,action,object_id,data) VALUES(?1,?2,?3,?4,?5)",
            params![id(), now(), action, object, serde_json::to_string(&data)?],
        )?;
        Ok(())
    }
}

#[cfg(test)]
mod migration_tests {
    use super::*;
    use rusqlite::Connection;
    use tempfile::TempDir;

    #[test]
    fn version_one_database_migrates_additive_quarantine_tables() {
        let temp = TempDir::new().unwrap();
        let root = temp.path().canonicalize().unwrap();
        std::fs::create_dir_all(&root).unwrap();
        let db_path = root.join("yingxu.sqlite");
        let connection = Connection::open(&db_path).unwrap();
        connection
            .execute_batch(include_str!("schema.sql"))
            .unwrap();
        connection.pragma_update(None, "user_version", 1).unwrap();
        drop(connection);

        let store = Store::open(&root).unwrap();
        let connection = store.connect().unwrap();
        let version: i32 = connection
            .query_row("PRAGMA user_version", [], |row| row.get(0))
            .unwrap();
        let table_count: i64 = connection.query_row(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name IN ('quarantine_items','quarantine_actions','file_action_previews')",
            [],
            |row| row.get(0),
        ).unwrap();
        assert_eq!(version, 3);
        assert_eq!(table_count, 3);
    }
}
