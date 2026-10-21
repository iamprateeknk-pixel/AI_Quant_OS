import sharp from 'sharp';

function validateMasks(maskRects, width, height) {
  for (const rect of maskRects) {
    if (!rect || !Number.isInteger(rect.x) || !Number.isInteger(rect.y)
      || !Number.isInteger(rect.width) || !Number.isInteger(rect.height)
      || rect.x < 0 || rect.y < 0 || rect.width < 1 || rect.height < 1
      || rect.x + rect.width > width || rect.y + rect.height > height) {
      throw new TypeError('mask rectangles must be positive integer bounds inside the image');
    }
  }
}

export async function compareVisualImages(referencePath, currentPath, options = {}) {
  const { diffPath, pixelThreshold = 0, maskRects = [] } = options;
  if (!Number.isInteger(pixelThreshold) || pixelThreshold < 0 || pixelThreshold > 255) {
    throw new TypeError('pixelThreshold must be an integer from 0 to 255');
  }

  const [reference, current] = await Promise.all([
    sharp(referencePath).ensureAlpha().raw().toBuffer({ resolveWithObject: true }),
    sharp(currentPath).ensureAlpha().raw().toBuffer({ resolveWithObject: true }),
  ]);
  const { width, height } = reference.info;
  if (current.info.width !== width || current.info.height !== height) {
    throw new Error(`dimensions differ: reference ${width}x${height}, current ${current.info.width}x${current.info.height}`);
  }
  validateMasks(maskRects, width, height);

  let comparedPixels = 0;
  let changedPixels = 0;
  let maxChannelDelta = 0;
  const diff = diffPath ? Buffer.alloc(width * height * 4) : undefined;
  for (let y = 0; y < height; y += 1) {
    for (let x = 0; x < width; x += 1) {
      const index = (y * width + x) * 4;
      const masked = maskRects.some((rect) => x >= rect.x && x < rect.x + rect.width
        && y >= rect.y && y < rect.y + rect.height);
      if (masked) {
        if (diff) diff.set([24, 24, 24, 255], index);
        continue;
      }
      comparedPixels += 1;
      let pixelDelta = 0;
      for (let channel = 0; channel < 4; channel += 1) {
        pixelDelta = Math.max(pixelDelta, Math.abs(reference.data[index + channel] - current.data[index + channel]));
      }
      maxChannelDelta = Math.max(maxChannelDelta, pixelDelta);
      const changed = pixelDelta > pixelThreshold;
      if (changed) changedPixels += 1;
      if (diff) diff.set(changed ? [255, 0, 180, 255] : [reference.data[index] >> 2, reference.data[index + 1] >> 2, reference.data[index + 2] >> 2, 255], index);
    }
  }

  if (comparedPixels === 0) throw new Error('masks exclude every image pixel');
  if (diffPath) await sharp(diff, { raw: { width, height, channels: 4 } }).png().toFile(diffPath);
  return {
    width,
    height,
    comparedPixels,
    changedPixels,
    changedRatio: comparedPixels === 0 ? 0 : changedPixels / comparedPixels,
    maxChannelDelta,
  };
}
