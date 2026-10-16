// Prepare a photo for upload: re-encode it in the browser before it leaves the device.
//
// Privacy: phone photos carry EXIF metadata (GPS location, time, device). Drawing the
// pixels onto a canvas and encoding a new JPEG keeps only the pixels, so the location
// of someone's home never ends up in a public bucket.
// Size: phone originals are often 3-10 MB; feeds and grids don't need more than about
// 1440 px, so uploads shrink to a few hundred KB.

export type PreparedImage = { blob: Blob; width: number; height: number; type: "image/jpeg" };

export async function prepareImage(file: File, maxSide: number): Promise<PreparedImage> {
  // "from-image" applies the EXIF rotation to the pixels before the EXIF is dropped.
  const bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
  try {
    const scale = Math.min(1, maxSide / Math.max(bitmap.width, bitmap.height));
    const width = Math.max(1, Math.round(bitmap.width * scale));
    const height = Math.max(1, Math.round(bitmap.height * scale));
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("no canvas");
    ctx.fillStyle = "#fff"; // JPEG has no transparency: put PNG cut-outs on white
    ctx.fillRect(0, 0, width, height);
    ctx.drawImage(bitmap, 0, 0, width, height);
    const blob = await new Promise<Blob | null>((done) => canvas.toBlob(done, "image/jpeg", 0.88));
    if (!blob) throw new Error("encode failed");
    return { blob, width, height, type: "image/jpeg" };
  } finally {
    bitmap.close();
  }
}
