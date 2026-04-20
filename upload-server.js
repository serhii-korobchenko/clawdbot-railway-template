import express from "express";
import multer from "multer";
import fs from "fs";
import path from "path";
import crypto from "crypto";

const app = express();

const PORT = process.env.UPLOAD_PORT || 8090;
const DOCS_INBOX_DIR = process.env.DOCS_INBOX_DIR || "/data/workspace/docs/inbox";
const UPLOAD_TOKEN = process.env.UPLOAD_TOKEN || "";
const MAX_UPLOAD_MB = parseInt(process.env.MAX_UPLOAD_MB || "25", 10);

const ALLOWED_EXT = new Set([".pdf", ".docx", ".txt", ".md"]);

fs.mkdirSync(DOCS_INBOX_DIR, { recursive: true });

function safeName(name) {
  return name.replace(/[^a-zA-Z0-9._-]/g, "_");
}

const storage = multer.diskStorage({
  destination: (req, file, cb) => cb(null, DOCS_INBOX_DIR),
  filename: (req, file, cb) => {
    const ext = path.extname(file.originalname).toLowerCase();
    const base = path.basename(file.originalname, ext);
    const ts = new Date().toISOString().replace(/[:.]/g, "-");
    const rnd = crypto.randomBytes(4).toString("hex");
    cb(null, `${safeName(base)}__${ts}__${rnd}${ext}`);
  },
});

const upload = multer({
  storage,
  limits: { fileSize: MAX_UPLOAD_MB * 1024 * 1024 },
  fileFilter: (req, file, cb) => {
    const ext = path.extname(file.originalname).toLowerCase();
    if (!ALLOWED_EXT.has(ext)) {
      return cb(new Error(`Unsupported file type: ${ext}`));
    }
    cb(null, true);
  },
});

function auth(req, res, next) {
  const token = req.header("x-upload-token");
  if (!UPLOAD_TOKEN || token !== UPLOAD_TOKEN) {
    return res.status(401).json({ ok: false, error: "unauthorized" });
  }
  next();
}

app.get("/health", (req, res) => {
  res.json({ ok: true, inbox: DOCS_INBOX_DIR });
});

app.post("/upload", auth, upload.single("file"), (req, res) => {
  if (!req.file) {
    return res.status(400).json({ ok: false, error: "file_missing" });
  }

  res.json({
    ok: true,
    stored_as: req.file.filename,
    original_name: req.file.originalname,
    size: req.file.size,
    path: req.file.path,
  });
});

app.use((err, req, res, next) => {
  res.status(400).json({
    ok: false,
    error: err.message || "upload_failed",
  });
});

app.listen(PORT, "0.0.0.0", () => {
  console.log(`[upload] listening on :${PORT}`);
});
