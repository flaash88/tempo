/**
 * A minimal PNG reader, so the audit can look at what was actually
 * painted rather than at what the DOM claims.
 *
 * The strip below the tab bar is a rendering question: does that band
 * show the bar's ground or the page's? getBoundingClientRect cannot
 * answer it — a box can be in the right place and paint nothing. Hence
 * real pixels.
 *
 * Handles what Playwright produces: 8 bits per channel, no interlacing,
 * RGB or RGBA. Anything else is refused rather than guessed at.
 */

import { inflateSync } from "node:zlib";

const SIGNATURE = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);

function paeth(a, b, c) {
  const p = a + b - c;
  const pa = Math.abs(p - a);
  const pb = Math.abs(p - b);
  const pc = Math.abs(p - c);
  if (pa <= pb && pa <= pc) return a;
  return pb <= pc ? b : c;
}

/** Undo the per-scanline filter PNG applies before compression. */
function unfilter(raw, width, height, bpp) {
  const stride = width * bpp;
  const out = Buffer.alloc(stride * height);
  let source = 0;
  for (let y = 0; y < height; y += 1) {
    const filter = raw[source];
    source += 1;
    const line = y * stride;
    const previous = line - stride;
    for (let x = 0; x < stride; x += 1) {
      const value = raw[source + x];
      const left = x >= bpp ? out[line + x - bpp] : 0;
      const up = y > 0 ? out[previous + x] : 0;
      const upLeft = y > 0 && x >= bpp ? out[previous + x - bpp] : 0;
      let restored;
      switch (filter) {
        case 0:
          restored = value;
          break;
        case 1:
          restored = value + left;
          break;
        case 2:
          restored = value + up;
          break;
        case 3:
          restored = value + ((left + up) >> 1);
          break;
        case 4:
          restored = value + paeth(left, up, upLeft);
          break;
        default:
          throw new Error(`unbekannter PNG-Filter ${filter} in Zeile ${y}`);
      }
      out[line + x] = restored & 0xff;
    }
    source += stride;
  }
  return out;
}

export function decodePng(buffer) {
  if (!buffer.subarray(0, 8).equals(SIGNATURE)) throw new Error("kein PNG");

  let offset = 8;
  let header = null;
  const data = [];
  while (offset < buffer.length) {
    const length = buffer.readUInt32BE(offset);
    const type = buffer.toString("ascii", offset + 4, offset + 8);
    const body = buffer.subarray(offset + 8, offset + 8 + length);
    if (type === "IHDR") {
      header = {
        width: body.readUInt32BE(0),
        height: body.readUInt32BE(4),
        bitDepth: body[8],
        colorType: body[9],
        interlace: body[12],
      };
    } else if (type === "IDAT") {
      data.push(body);
    } else if (type === "IEND") {
      break;
    }
    offset += 12 + length;
  }

  if (!header) throw new Error("PNG ohne IHDR");
  if (header.bitDepth !== 8) throw new Error(`PNG mit ${header.bitDepth} Bit`);
  if (header.interlace !== 0) throw new Error("PNG ist interlaced");
  const channels = header.colorType === 6 ? 4 : header.colorType === 2 ? 3 : 0;
  if (!channels) throw new Error(`PNG-Farbtyp ${header.colorType}`);

  const pixels = unfilter(
    inflateSync(Buffer.concat(data)),
    header.width,
    header.height,
    channels,
  );

  return {
    width: header.width,
    height: header.height,
    channels,
    /** The pixel at (x, y) as [r, g, b]. */
    at(x, y) {
      const index = (y * header.width + x) * channels;
      return [pixels[index], pixels[index + 1], pixels[index + 2]];
    },
  };
}

/** How far apart two colours are, per channel, at most. */
export function distance(a, b) {
  return Math.max(Math.abs(a[0] - b[0]), Math.abs(a[1] - b[1]), Math.abs(a[2] - b[2]));
}

export const hex = (colour) =>
  "#" + colour.map((channel) => channel.toString(16).padStart(2, "0")).join("");
