/**
 * Convert top-level English SVGs in docs/SVG → docs/PNG
 */
const fs = require("fs");
const path = require("path");
const { Resvg } = require("@resvg/resvg-js");

const srcDir = path.resolve(__dirname, "../../docs/SVG");
const outDir = path.resolve(__dirname, "../../docs/PNG");

fs.mkdirSync(outDir, { recursive: true });

const files = fs
  .readdirSync(srcDir, { withFileTypes: true })
  .filter((d) => d.isFile() && d.name.toLowerCase().endsWith(".svg"))
  .map((d) => d.name)
  .sort();

if (!files.length) {
  console.error("No SVG files found in", srcDir);
  process.exit(1);
}

for (const name of files) {
  const svgPath = path.join(srcDir, name);
  const pngName = name.replace(/\.svg$/i, ".png");
  const pngPath = path.join(outDir, pngName);
  const svg = fs.readFileSync(svgPath);

  const resvg = new Resvg(svg, {
    fitTo: {
      mode: "width",
      // 4× design canvas (1920) for sharper print / zoom
      value: 7680,
    },
    background: "white",
  });
  const pngData = resvg.render().asPng();
  fs.writeFileSync(pngPath, pngData);
  console.log(`[ok] ${name} → PNG/${pngName} (${pngData.length} bytes)`);
}

console.log(`[done] ${files.length} PNG → ${outDir}`);
