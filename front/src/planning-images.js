// Compression is separate from upload. Blob output is unsaved until the host confirms.
export function scaledSize(width, height) {
  if (!(width > 0 && height > 0)) throw new Error('图片尺寸无效。');
  const scale = Math.min(1,1000 / Math.max(width,height));
  return {width:Math.max(1,Math.round(width * scale)),height:Math.max(1,Math.round(height * scale))};
}
export async function compressImage(file, {decode = blob => createImageBitmap(blob), makeCanvas = () => document.createElement('canvas')} = {}) {
  if (!['image/jpeg','image/png','image/webp'].includes(file.type)) throw new Error('请选择图片文件。');
  let bitmap;
  try {
    bitmap = await decode(file);
    const size = scaledSize(bitmap.width,bitmap.height), canvas = makeCanvas();
    canvas.width = size.width; canvas.height = size.height;
    const context = canvas.getContext('2d');
    if (!context) throw new Error('当前浏览器无法压缩图片。');
    context.fillStyle = '#ffffff'; context.fillRect(0,0,size.width,size.height);
    context.drawImage(bitmap,0,0,size.width,size.height);
    const blob = await new Promise((resolve,reject) => canvas.toBlob(value => value ? resolve(value) : reject(new Error('图片压缩失败。')), 'image/jpeg',0.72));
    if (blob.type !== 'image/jpeg') throw new Error('当前浏览器不支持JPEG压缩。');
    return {blob,name:(file.name || '图片').replace(/\.[^.]+$/,'') + '.jpg',...size};
  } catch (error) {
    throw new Error(error.message || '图片无法解码，请重新选择。');
  } finally { bitmap?.close?.(); }
}
export function protectedImageUrl(value, origin = globalThis.location?.origin) {
  // Only host-provided same-origin protected references. No remote/data URLs.
  if (!value || !origin) return null;
  try { const url = new URL(value,origin); return url.origin === origin && ['http:','https:'].includes(url.protocol) ? url.href : null; }
  catch { return null; }
}

export function displayImageUrl(value) {
  // Blob URLs are produced only by the authenticated host image loader.
  if (typeof value === 'string' && value.startsWith('blob:')) return value;
  return protectedImageUrl(value);
}
