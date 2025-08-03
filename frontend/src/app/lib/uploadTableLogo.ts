import { uploadImage } from "./uploadImage";

export async function uploadTableLogo(file: File, tableId: number) {
  return uploadImage(file, "tables", `${tableId}/logo`, "logo");
}
