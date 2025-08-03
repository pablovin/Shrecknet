import type { NextApiRequest, NextApiResponse } from "next";
import * as formidable from "formidable";
import fs from "fs";
import path from "path";

export const config = {
  api: { bodyParser: false },
};

export default function handler(req: NextApiRequest, res: NextApiResponse) {
  if (req.method !== "POST") return res.status(405).end();

  const form = new formidable.IncomingForm();

  form.parse(req, (err, fields, files) => {
    if (err) {
      res.status(400).json({ error: String(err) });
      return;
    }

    let file = files.file as formidable.File | formidable.File[] | undefined;
    if (Array.isArray(file)) file = file[0];

    const pageType = Array.isArray(fields.pageType)
      ? fields.pageType[0]
      : fields.pageType || "misc";
    const pageName = Array.isArray(fields.pageName)
      ? fields.pageName[0]
      : fields.pageName || "general";
    const customFileName = Array.isArray(fields.customFileName)
      ? fields.customFileName[0]
      : fields.customFileName;

    if (!file) {
      res.status(400).json({ error: "No file uploaded" });
      return;
    }

    const isTable = pageType === "tables";
    const dir = isTable
      ? path.join(process.cwd(), "uploads", "tables", pageName)
      : path.join(process.cwd(), "uploads/images", pageType, pageName);
    fs.mkdirSync(dir, { recursive: true });

    // File extension
    const fileExt = path
      .extname(file.originalFilename || file.name || "upload")
      .toLowerCase();

    // Clean base name
    const baseName = (file.originalFilename || file.name || "upload").replace(
      /[^a-zA-Z0-9_.-]/g,
      "_",
    );

    // Use customFileName if present, otherwise use the base name so repeated
    // uploads replace the previous file
    const fileName = customFileName
      ? `${customFileName}${fileExt}`
      : `${baseName}`;
    const dest = path.join(dir, fileName);

    // Delete unknown old file with the same name to avoid duplicates
    if (fs.existsSync(dest)) fs.unlinkSync(dest);

    // Robust: check for filepath (formidable v3+), fallback to path (v2)
    const tempPath =
      (file as formidable.File & { filepath?: string }).filepath ||
      (file as formidable.File & { path?: string }).path;
    if (!tempPath) {
      res.status(400).json({ error: "File path not found for upload" });
      return;
    }

    // --- PATCH: Use copy + unlink instead of rename ---
    try {
      fs.copyFileSync(tempPath, dest);
      fs.unlinkSync(tempPath);
    } catch (copyErr) {
      res.status(500).json({ error: `Error saving file: ${String(copyErr)}` });
      return;
    }
    // -------------------------------------------------

    const urlBase = isTable ? "/uploads/tables" : "/uploads/images";
    res.status(200).json({
      url: path.join(urlBase, pageName, fileName).replace(/\\+/g, "/"),
    });
  });
}
