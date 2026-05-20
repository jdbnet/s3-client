"""
S3 web client - Flask backend with MariaDB credentials and S3 operations.
"""
from __future__ import annotations

import hmac
import logging
import os
import posixpath
from contextlib import contextmanager
from datetime import timedelta
from typing import Any, Iterable

import boto3
from botocore.config import Config
import mysql.connector
from mysql.connector import pooling
from dotenv import load_dotenv
from flask import Flask, Response, abort, jsonify, request, send_file, send_from_directory, session
from werkzeug.utils import safe_join, secure_filename

load_dotenv()

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger("s3_web")

app = Flask(__name__, static_folder=None)
app.secret_key = os.getenv("SECRET_KEY", "dev-change-me")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(
    days=int(os.getenv("SESSION_DAYS", "14"))
)
app.config["VERSION"] = os.getenv("VERSION", "unknown")
if os.getenv("SESSION_COOKIE_SECURE", "").lower() in ("1", "true", "yes"):
    app.config["SESSION_COOKIE_SECURE"] = True

_db_pool: pooling.MySQLConnectionPool | None = None


def get_pool() -> pooling.MySQLConnectionPool:
    global _db_pool
    if _db_pool is None:
        _db_pool = pooling.MySQLConnectionPool(
            pool_name="s3_web_pool",
            pool_size=int(os.getenv("MYSQL_POOL_SIZE", "5")),
            host=os.getenv("MYSQL_HOST", "127.0.0.1"),
            port=int(os.getenv("MYSQL_PORT", "3306")),
            user=os.getenv("MYSQL_USER", "root"),
            password=os.getenv("MYSQL_PASSWORD", ""),
            database=os.getenv("MYSQL_DATABASE", "s3_web"),
        )
    return _db_pool


@contextmanager
def db_cursor(dict_cursor: bool = True):
    pool = get_pool()
    conn = pool.get_connection()
    try:
        cur = conn.cursor(dictionary=dict_cursor)
        yield conn, cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def _aes_key() -> str:
    key = os.getenv("DB_ENCRYPTION_KEY") or os.getenv("CREDENTIALS_ENCRYPTION_KEY")
    if not key:
        raise RuntimeError("DB_ENCRYPTION_KEY is not set")
    return key


def init_db() -> None:
    ddl = """
    CREATE TABLE IF NOT EXISTS s3_folders (
      id INT AUTO_INCREMENT PRIMARY KEY,
      parent_id INT NULL,
      label VARCHAR(255) NOT NULL,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      CONSTRAINT fk_s3_folder_parent FOREIGN KEY (parent_id)
        REFERENCES s3_folders(id) ON DELETE CASCADE
    );
    CREATE TABLE IF NOT EXISTS s3_accounts (
      id INT AUTO_INCREMENT PRIMARY KEY,
      folder_id INT NULL,
      label VARCHAR(255) NOT NULL,
      endpoint_url VARCHAR(1024) NULL,
      region VARCHAR(255) NULL,
      force_path_style TINYINT(1) NOT NULL DEFAULT 0,
      access_key_enc VARBINARY(512) NOT NULL,
      secret_key_enc VARBINARY(512) NOT NULL,
      session_token_enc VARBINARY(1024) NULL,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
      CONSTRAINT fk_s3_account_folder FOREIGN KEY (folder_id)
        REFERENCES s3_folders(id) ON DELETE SET NULL
    );
    """
    with db_cursor() as (_, cur):
        for stmt in ddl.split(";"):
            s = stmt.strip()
            if s:
                cur.execute(s)


def _like_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _folder_subtree_ids(cur, root_id: int) -> list[int]:
    cur.execute(
        """
        WITH RECURSIVE sub AS (
          SELECT id FROM s3_folders WHERE id = %s
          UNION ALL
          SELECT f.id FROM s3_folders f INNER JOIN sub ON f.parent_id = sub.id
        )
        SELECT id FROM sub
        """,
        (root_id,),
    )
    return [r["id"] for r in cur.fetchall()]


def _folder_breadcrumb_rows(cur, folder_id: int) -> list[dict[str, Any]]:
    cur.execute(
        """
        WITH RECURSIVE up AS (
          SELECT id, parent_id, label FROM s3_folders WHERE id = %s
          UNION ALL
          SELECT f.id, f.parent_id, f.label
          FROM s3_folders f INNER JOIN up ON f.id = up.parent_id
        )
        SELECT id, label FROM up
        """,
        (folder_id,),
    )
    rows = cur.fetchall()
    return list(reversed(rows))


def _web_login_ok(username: str, password: str) -> bool:
    u = os.getenv("WEBAPP_USERNAME", "")
    expected = os.getenv("WEBAPP_PASSWORD", "")
    if not u or not expected or username != u:
        return False
    pa = password.encode("utf-8")
    pb = expected.encode("utf-8")
    if len(pa) != len(pb):
        return False
    return hmac.compare_digest(pa, pb)


def require_login(fn):
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            return jsonify({"error": "unauthorized"}), 401
        return fn(*args, **kwargs)

    wrapped.__name__ = fn.__name__
    return wrapped


def _decode_db_value(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, (bytes, bytearray)):
        return v.decode("utf-8", errors="replace")
    return str(v)


def _load_account_credentials(account_id: int) -> dict[str, Any] | None:
    key = _aes_key()
    with db_cursor() as (_, cur):
        cur.execute(
            """
            SELECT id, label, endpoint_url, region, force_path_style,
                   AES_DECRYPT(access_key_enc, %s) AS access_key,
                   AES_DECRYPT(secret_key_enc, %s) AS secret_key,
                   AES_DECRYPT(session_token_enc, %s) AS session_token
            FROM s3_accounts
            WHERE id = %s
            """,
            (key, key, key, account_id),
        )
        row = cur.fetchone()
    if not row:
        return None
    row["access_key"] = _decode_db_value(row.get("access_key"))
    row["secret_key"] = _decode_db_value(row.get("secret_key"))
    row["session_token"] = _decode_db_value(row.get("session_token"))
    row["force_path_style"] = bool(row.get("force_path_style"))
    return row


def _s3_client(account: dict[str, Any]):
    endpoint_url = (account.get("endpoint_url") or "").strip() or None
    region = (account.get("region") or "").strip() or None
    addressing_style = "path" if account.get("force_path_style") else "virtual"
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        region_name=region,
        aws_access_key_id=account.get("access_key"),
        aws_secret_access_key=account.get("secret_key"),
        aws_session_token=account.get("session_token") or None,
        config=Config(s3={"addressing_style": addressing_style}),
    )


def _normalize_path(path: str) -> str:
    if not path or path == "/":
        return ""
    p = path.strip("/")
    if not p:
        return ""
    if not p.endswith("/"):
        p += "/"
    return p


def _join_key(prefix: str, name: str) -> str:
    if not prefix:
        return name
    return posixpath.join(prefix, name)


def _paginate_list_objects(client, bucket: str, prefix: str, delimiter: str = "/") -> dict[str, Any]:
    params = {
        "Bucket": bucket,
        "Prefix": prefix,
        "Delimiter": delimiter,
    }
    resp = client.list_objects_v2(**params)
    return resp


@app.route("/api/login", methods=["POST"])
def api_login():
    body = request.get_json(silent=True) or {}
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    if _web_login_ok(username, password):
        session["logged_in"] = True
        session.permanent = bool(os.getenv("SESSION_PERMANENT", "1").lower() in ("1", "true", "yes"))
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "invalid credentials"}), 401


@app.route("/api/logout", methods=["POST"])
def api_logout():
    session.pop("logged_in", None)
    return jsonify({"ok": True})


@app.route("/api/me", methods=["GET"])
def api_me():
    version = app.config.get("VERSION", "unknown")
    if session.get("logged_in"):
        return jsonify({"logged_in": True, "app_version": version})
    return jsonify({"logged_in": False, "app_version": version})


@app.route("/api/folders", methods=["GET"])
@require_login
def list_all_folders():
    with db_cursor() as (_, cur):
        cur.execute(
            "SELECT id, label, parent_id FROM s3_folders ORDER BY label"
        )
        rows = cur.fetchall()
    return jsonify({"items": rows})


@app.route("/api/folders", methods=["POST"])
@require_login
def create_folder():
    body = request.get_json(silent=True) or {}
    label = (body.get("label") or "").strip()
    if not label:
        return jsonify({"error": "label required"}), 400
    pid = body.get("parent_id")
    parent_id = int(pid) if pid is not None and pid != "" else None
    if parent_id is not None:
        with db_cursor() as (_, cur):
            cur.execute("SELECT id FROM s3_folders WHERE id = %s", (parent_id,))
            if not cur.fetchone():
                return jsonify({"error": "parent not found"}), 400
    with db_cursor() as (_, cur):
        cur.execute(
            "INSERT INTO s3_folders (label, parent_id) VALUES (%s, %s)",
            (label, parent_id),
        )
        fid = cur.lastrowid
    return jsonify({"id": fid}), 201


@app.route("/api/folders/<int:fid>", methods=["PATCH"])
@require_login
def update_folder(fid: int):
    body = request.get_json(silent=True) or {}
    with db_cursor() as (_, cur):
        cur.execute("SELECT id, parent_id, label FROM s3_folders WHERE id = %s", (fid,))
        row = cur.fetchone()
    if not row:
        return jsonify({"error": "not found"}), 404
    sets = []
    args: list[Any] = []
    if "label" in body:
        sets.append("label = %s")
        args.append(str(body["label"]).strip())
    if "parent_id" in body:
        p = body["parent_id"]
        new_parent = int(p) if p is not None and p != "" else None
        if new_parent == fid:
            return jsonify({"error": "cannot set parent to self"}), 400
        with db_cursor() as (_, cur):
            if new_parent is not None:
                cur.execute("SELECT id FROM s3_folders WHERE id = %s", (new_parent,))
                if not cur.fetchone():
                    return jsonify({"error": "parent not found"}), 400
            sub = _folder_subtree_ids(cur, fid)
        if new_parent is not None and new_parent in sub:
            return jsonify({"error": "cannot move folder into its descendant"}), 400
        sets.append("parent_id = %s")
        args.append(new_parent)
    if not sets:
        return jsonify({"ok": True})
    args.append(fid)
    with db_cursor() as (_, cur):
        cur.execute(
            f"UPDATE s3_folders SET {', '.join(sets)} WHERE id = %s",
            tuple(args),
        )
    return jsonify({"ok": True})


@app.route("/api/folders/<int:fid>", methods=["DELETE"])
@require_login
def delete_folder(fid: int):
    with db_cursor() as (_, cur):
        cur.execute("DELETE FROM s3_folders WHERE id = %s", (fid,))
        if cur.rowcount == 0:
            return jsonify({"error": "not found"}), 404
    return jsonify({"ok": True})


def _account_select_sql(extra_where: str = "") -> str:
    return f"""
            SELECT a.id, a.folder_id, a.label, a.endpoint_url, a.region,
                   a.force_path_style, a.created_at, a.updated_at,
                   pf.label AS folder_label
            FROM s3_accounts a
            LEFT JOIN s3_folders pf ON pf.id = a.folder_id
            {extra_where}
            """


@app.route("/api/browse", methods=["GET"])
@require_login
def api_browse():
    raw_fid = request.args.get("folder_id")
    if raw_fid in (None, "", "root"):
        folder_id = None
    else:
        try:
            folder_id = int(raw_fid)
        except (TypeError, ValueError):
            return jsonify({"error": "invalid folder_id"}), 400
    q = (request.args.get("q") or "").strip()
    esc = _like_escape(q) if q else ""
    pat = f"%{esc}%" if q else ""

    with db_cursor() as (_, cur):
        breadcrumb: list[dict[str, Any]] = []
        if folder_id is not None:
            cur.execute("SELECT id FROM s3_folders WHERE id = %s", (folder_id,))
            if not cur.fetchone():
                return jsonify({"error": "folder not found"}), 404
            breadcrumb = _folder_breadcrumb_rows(cur, folder_id)

        if q:
            if folder_id is None:
                cur.execute(
                    _account_select_sql(
                        "WHERE (a.label LIKE %s ESCAPE '\\\\' OR a.endpoint_url LIKE %s ESCAPE '\\\\')"
                    )
                    + " ORDER BY a.label",
                    (pat, pat),
                )
                accounts = cur.fetchall()
            else:
                ids = _folder_subtree_ids(cur, folder_id)
                if not ids:
                    accounts = []
                else:
                    ph = ",".join(["%s"] * len(ids))
                    cur.execute(
                        _account_select_sql(
                            f"WHERE a.folder_id IN ({ph}) AND "
                            "(a.label LIKE %s ESCAPE '\\\\' OR a.endpoint_url LIKE %s ESCAPE '\\\\')"
                        )
                        + " ORDER BY a.label",
                        (*ids, pat, pat),
                    )
                    accounts = cur.fetchall()
            return jsonify(
                {
                    "breadcrumb": breadcrumb,
                    "folders": [],
                    "accounts": accounts,
                    "search_active": True,
                }
            )

        if folder_id is None:
            cur.execute(
                "SELECT id, label, parent_id FROM s3_folders WHERE parent_id IS NULL ORDER BY label"
            )
            folders = cur.fetchall()
            cur.execute(
                _account_select_sql("WHERE a.folder_id IS NULL") + " ORDER BY a.label"
            )
        else:
            cur.execute(
                "SELECT id, label, parent_id FROM s3_folders WHERE parent_id = %s ORDER BY label",
                (folder_id,),
            )
            folders = cur.fetchall()
            cur.execute(
                _account_select_sql("WHERE a.folder_id = %s") + " ORDER BY a.label",
                (folder_id,),
            )
        accounts = cur.fetchall()

    return jsonify(
        {
            "breadcrumb": breadcrumb,
            "folders": folders,
            "accounts": accounts,
            "search_active": False,
        }
    )


@app.route("/api/accounts", methods=["GET"])
@require_login
def list_accounts():
    with db_cursor() as (_, cur):
        cur.execute(_account_select_sql("") + " ORDER BY a.label")
        rows = cur.fetchall()
    return jsonify({"items": rows})


@app.route("/api/accounts", methods=["POST"])
@require_login
def create_account():
    body = request.get_json(silent=True) or {}
    label = (body.get("label") or "").strip()
    if not label:
        return jsonify({"error": "label required"}), 400

    endpoint_url = (body.get("endpoint_url") or "").strip() or None
    region = (body.get("region") or "").strip() or None
    force_path_style = bool(body.get("force_path_style"))

    access_key = (body.get("access_key") or "").strip()
    secret_key = (body.get("secret_key") or "").strip()
    session_token = (body.get("session_token") or "").strip()

    if not access_key or not secret_key:
        return jsonify({"error": "access_key and secret_key required"}), 400

    fid = body.get("folder_id")
    folder_id = int(fid) if fid is not None and fid != "" else None
    if folder_id is not None:
        with db_cursor() as (_, cur):
            cur.execute("SELECT id FROM s3_folders WHERE id = %s", (folder_id,))
            if not cur.fetchone():
                return jsonify({"error": "folder not found"}), 400

    key = _aes_key()
    with db_cursor() as (_, cur):
        cur.execute(
            """
            INSERT INTO s3_accounts (
                folder_id, label, endpoint_url, region, force_path_style,
                access_key_enc, secret_key_enc, session_token_enc
            )
            VALUES (
                %s, %s, %s, %s, %s,
                AES_ENCRYPT(%s, %s),
                AES_ENCRYPT(%s, %s),
                CASE WHEN %s = '' THEN NULL ELSE AES_ENCRYPT(%s, %s) END
            )
            """,
            (
                folder_id,
                label,
                endpoint_url,
                region,
                int(force_path_style),
                access_key,
                key,
                secret_key,
                key,
                session_token,
                session_token,
                key,
            ),
        )
        aid = cur.lastrowid
    return jsonify({"id": aid}), 201


@app.route("/api/accounts/<int:aid>", methods=["PATCH"])
@require_login
def update_account(aid: int):
    body = request.get_json(silent=True) or {}
    fields = []
    args: list[Any] = []

    if "label" in body:
        fields.append("label = %s")
        args.append(str(body["label"]).strip())
    if "endpoint_url" in body:
        val = (body.get("endpoint_url") or "").strip() or None
        fields.append("endpoint_url = %s")
        args.append(val)
    if "region" in body:
        val = (body.get("region") or "").strip() or None
        fields.append("region = %s")
        args.append(val)
    if "force_path_style" in body:
        fields.append("force_path_style = %s")
        args.append(1 if body.get("force_path_style") else 0)
    if "folder_id" in body:
        p = body.get("folder_id")
        folder_id = int(p) if p is not None and p != "" else None
        if folder_id is not None:
            with db_cursor() as (_, cur):
                cur.execute("SELECT id FROM s3_folders WHERE id = %s", (folder_id,))
                if not cur.fetchone():
                    return jsonify({"error": "folder not found"}), 400
        fields.append("folder_id = %s")
        args.append(folder_id)

    key = _aes_key()
    if "access_key" in body:
        fields.append("access_key_enc = AES_ENCRYPT(%s, %s)")
        args.extend([str(body.get("access_key") or ""), key])
    if "secret_key" in body:
        fields.append("secret_key_enc = AES_ENCRYPT(%s, %s)")
        args.extend([str(body.get("secret_key") or ""), key])
    if "session_token" in body:
        st = str(body.get("session_token") or "")
        fields.append("session_token_enc = CASE WHEN %s = '' THEN NULL ELSE AES_ENCRYPT(%s, %s) END")
        args.extend([st, st, key])

    if not fields:
        return jsonify({"ok": True})

    args.append(aid)
    with db_cursor() as (_, cur):
        cur.execute(
            f"UPDATE s3_accounts SET {', '.join(fields)} WHERE id = %s",
            tuple(args),
        )
        if cur.rowcount == 0:
            return jsonify({"error": "not found"}), 404
    return jsonify({"ok": True})


@app.route("/api/accounts/<int:aid>", methods=["DELETE"])
@require_login
def delete_account(aid: int):
    with db_cursor() as (_, cur):
        cur.execute("DELETE FROM s3_accounts WHERE id = %s", (aid,))
        if cur.rowcount == 0:
            return jsonify({"error": "not found"}), 404
    return jsonify({"ok": True})


@app.route("/api/s3/<int:aid>/buckets", methods=["GET"])
@require_login
def list_buckets(aid: int):
    account = _load_account_credentials(aid)
    if not account:
        return jsonify({"error": "account not found"}), 404
    try:
        client = _s3_client(account)
        resp = client.list_buckets()
        buckets = []
        for b in resp.get("Buckets", []):
            buckets.append({"name": b.get("Name"), "created_at": b.get("CreationDate")})
        return jsonify({"items": buckets})
    except Exception as e:
        log.exception("list buckets failed")
        return jsonify({"error": str(e)}), 400


def _list_objects(client, bucket: str, path: str, q: str = "") -> dict[str, Any]:
    prefix = _normalize_path(path)
    query = q.strip().lower()
    if query:
        paginator = client.get_paginator("list_objects_v2")
        pages = paginator.paginate(Bucket=bucket, Prefix=prefix)
        resp = {"Contents": [], "CommonPrefixes": []}
        for page in pages:
            for obj in page.get("Contents", []) or []:
                key = obj.get("Key") or ""
                if key.endswith("/"):
                    continue
                name = key.split("/")[-1].lower()
                if query in key.lower() or query in name:
                    resp["Contents"].append(obj)
        folders: list[dict[str, str]] = []
    else:
        resp = _paginate_list_objects(client, bucket, prefix)
        folders = []
        for cp in resp.get("CommonPrefixes", []) or []:
            p = cp.get("Prefix") or ""
            name = p.rstrip("/").split("/")[-1] if p else ""
            if p:
                folders.append({"prefix": p, "name": name})
    files = []
    for obj in resp.get("Contents", []) or []:
        key = obj.get("Key")
        if not key:
            continue
        if key.endswith("/"):
            continue
        name = key.split("/")[-1]
        files.append(
            {
                "key": key,
                "name": name,
                "size": int(obj.get("Size") or 0),
                "last_modified": obj.get("LastModified"),
            }
        )
    return {"prefix": prefix, "folders": folders, "files": files}


@app.route("/api/s3/<int:aid>/objects/list", methods=["POST"])
@require_login
def list_objects(aid: int):
    body = request.get_json(silent=True) or {}
    bucket = (body.get("bucket") or "").strip()
    path = (body.get("path") or "").strip()
    q = (body.get("q") or "").strip()
    if not bucket:
        return jsonify({"error": "bucket required"}), 400
    account = _load_account_credentials(aid)
    if not account:
        return jsonify({"error": "account not found"}), 404
    try:
        client = _s3_client(account)
        data = _list_objects(client, bucket, path, q)
        return jsonify({"bucket": bucket, "path": path or "/", **data})
    except Exception as e:
        log.exception("list objects failed")
        return jsonify({"error": str(e)}), 400


@app.route("/api/s3/<int:aid>/objects/mkdir", methods=["POST"])
@require_login
def create_prefix(aid: int):
    body = request.get_json(silent=True) or {}
    bucket = (body.get("bucket") or "").strip()
    path = (body.get("path") or "").strip()
    name = (body.get("name") or "").strip().strip("/")
    if not bucket or not name:
        return jsonify({"error": "bucket and name required"}), 400
    account = _load_account_credentials(aid)
    if not account:
        return jsonify({"error": "account not found"}), 404
    prefix = _normalize_path(path)
    key = _join_key(prefix, f"{name}/")
    try:
        client = _s3_client(account)
        client.put_object(Bucket=bucket, Key=key, Body=b"")
        return jsonify({"ok": True, "key": key})
    except Exception as e:
        log.exception("mkdir failed")
        return jsonify({"error": str(e)}), 400


def _delete_objects(client, bucket: str, keys: Iterable[str]) -> None:
    batch = []
    for k in keys:
        batch.append({"Key": k})
        if len(batch) == 1000:
            client.delete_objects(Bucket=bucket, Delete={"Objects": batch})
            batch = []
    if batch:
        client.delete_objects(Bucket=bucket, Delete={"Objects": batch})


@app.route("/api/s3/<int:aid>/objects/delete", methods=["POST"])
@require_login
def delete_object(aid: int):
    body = request.get_json(silent=True) or {}
    bucket = (body.get("bucket") or "").strip()
    key = (body.get("key") or "").strip()
    if not bucket or not key:
        return jsonify({"error": "bucket and key required"}), 400
    account = _load_account_credentials(aid)
    if not account:
        return jsonify({"error": "account not found"}), 404
    try:
        client = _s3_client(account)
        if key.endswith("/"):
            resp = client.list_objects_v2(Bucket=bucket, Prefix=key)
            keys = [o.get("Key") for o in resp.get("Contents", []) or [] if o.get("Key")]
            _delete_objects(client, bucket, keys)
        else:
            client.delete_object(Bucket=bucket, Key=key)
        return jsonify({"ok": True})
    except Exception as e:
        log.exception("delete failed")
        return jsonify({"error": str(e)}), 400


@app.route("/api/s3/<int:aid>/objects/rename", methods=["POST"])
@require_login
def rename_object(aid: int):
    body = request.get_json(silent=True) or {}
    bucket = (body.get("bucket") or "").strip()
    old_key = (body.get("old_key") or "").strip()
    new_key = (body.get("new_key") or "").strip()
    if not bucket or not old_key or not new_key:
        return jsonify({"error": "bucket, old_key, new_key required"}), 400
    account = _load_account_credentials(aid)
    if not account:
        return jsonify({"error": "account not found"}), 404
    try:
        client = _s3_client(account)
        if old_key.endswith("/"):
            resp = client.list_objects_v2(Bucket=bucket, Prefix=old_key)
            keys = [o.get("Key") for o in resp.get("Contents", []) or [] if o.get("Key")]
            for k in keys:
                suffix = k[len(old_key):]
                dest = new_key + suffix
                client.copy_object(Bucket=bucket, CopySource={"Bucket": bucket, "Key": k}, Key=dest)
            _delete_objects(client, bucket, keys)
        else:
            client.copy_object(Bucket=bucket, CopySource={"Bucket": bucket, "Key": old_key}, Key=new_key)
            client.delete_object(Bucket=bucket, Key=old_key)
        return jsonify({"ok": True})
    except Exception as e:
        log.exception("rename failed")
        return jsonify({"error": str(e)}), 400


@app.route("/api/s3/<int:aid>/objects/upload", methods=["POST"])
@require_login
def upload_object(aid: int):
    bucket = (request.form.get("bucket") or "").strip()
    path = (request.form.get("path") or "").strip()
    if not bucket:
        return jsonify({"error": "bucket required"}), 400
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "file required"}), 400
    safe_name = secure_filename(f.filename)
    if not safe_name:
        return jsonify({"error": "invalid filename"}), 400
    prefix = _normalize_path(path)
    key = _join_key(prefix, safe_name)
    account = _load_account_credentials(aid)
    if not account:
        return jsonify({"error": "account not found"}), 404
    try:
        client = _s3_client(account)
        client.upload_fileobj(f.stream, bucket, key)
        return jsonify({"ok": True, "key": key})
    except Exception as e:
        log.exception("upload failed")
        return jsonify({"error": str(e)}), 400


@app.route("/api/s3/<int:aid>/objects/download", methods=["GET"])
@require_login
def download_object(aid: int):
    bucket = (request.args.get("bucket") or "").strip()
    key = (request.args.get("key") or "").strip()
    if not bucket or not key:
        abort(400)
    account = _load_account_credentials(aid)
    if not account:
        abort(404)
    try:
        client = _s3_client(account)
        obj = client.get_object(Bucket=bucket, Key=key)
        body = obj.get("Body")
        if body is None:
            abort(404)

        def gen():
            for chunk in iter(lambda: body.read(65536), b""):
                yield chunk

        name = posixpath.basename(key) or "download"
        return Response(
            gen(),
            mimetype="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )
    except Exception:
        abort(400)


STATIC_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
DIST = os.path.join(STATIC_ROOT, "dist")
PWA_STATIC_FILES = frozenset({"manifest.webmanifest", "sw.js"})


@app.route("/assets/<path:sub>")
def spa_assets(sub):
    return send_from_directory(os.path.join(DIST, "assets"), sub)


@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def spa(path):
    if path.startswith("api"):
        abort(404)
    index = os.path.join(DIST, "index.html")
    if not os.path.isfile(index):
        return (
            "Frontend not built. Run: cd frontend && npm ci && npm run build",
            503,
        )
    if path in PWA_STATIC_FILES:
        try:
            pwa_path = safe_join(STATIC_ROOT, path)
        except ValueError:
            pwa_path = None
        if pwa_path and os.path.isfile(pwa_path):
            if path.endswith(".webmanifest"):
                return send_file(pwa_path, mimetype="application/manifest+json")
            return send_file(pwa_path, mimetype="application/javascript")
    if path:
        try:
            file_path = safe_join(DIST, path)
        except ValueError:
            file_path = None
        if file_path and os.path.isfile(file_path):
            if path.endswith(".webmanifest"):
                return send_file(file_path, mimetype="application/manifest+json")
            if path.endswith(".js"):
                return send_file(file_path, mimetype="application/javascript")
            return send_file(file_path)
    return send_from_directory(DIST, "index.html")


with app.app_context():
    try:
        init_db()
    except Exception as e:
        log.warning("init_db skipped (DB unavailable): %s", e)


if __name__ == "__main__":
    app.run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
        debug=os.getenv("FLASK_DEBUG", "").lower() in ("1", "true", "yes"),
    )
