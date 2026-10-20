mermaid.initialize({
  startOnLoad: false,
  securityLevel: "strict",
  htmlLabels: false,
  suppressErrorRendering: true,
  maxTextSize: 12000,
  maxEdges: 12,
  theme: "base",
  fontFamily: '"Microsoft YaHei UI", sans-serif',
  flowchart: { htmlLabels: false, useMaxWidth: false, curve: "linear", padding: 16 },
  themeVariables: {
    primaryColor: "#f1e2d3", primaryTextColor: "#2e2018", primaryBorderColor: "#9c4520",
    lineColor: "#9c4520", edgeLabelBackground: "#faf3e7", tertiaryColor: "#efe7dc", fontSize: "16px"
  }
});

const PAPER = "#faf3e7";

window.renderDiagram = async function (source, width = 700) {
  const started = performance.now();
  const container = document.getElementById("graph");
  let imageUrl;
  try {
    if (source.length > 12000 || !/^flowchart (TD|TB|LR)\n/.test(source) || /[<{};&\\]/.test(source)) {
      throw new Error("Unsupported input");
    }
    container.replaceChildren();
    const result = await mermaid.render("textlensGraph", source, container);
    const documentSvg = new DOMParser().parseFromString(result.svg, "image/svg+xml");
    const svg = documentSvg.documentElement;
    if (svg.querySelector("script, foreignObject, image, a") || svg.nodeName !== "svg") {
      throw new Error("Unsupported SVG");
    }
    const bounds = svg.getAttribute("viewBox").split(/\s+/).map(Number);
    const height = Math.max(100, Math.min(2200, Math.ceil(width * bounds[3] / bounds[2])));
    svg.setAttribute("width", String(width));
    svg.setAttribute("height", String(height));
    svg.setAttribute("style", `background:${PAPER}`);
    const serialized = new XMLSerializer().serializeToString(svg);
    const image = new Image();
    imageUrl = URL.createObjectURL(new Blob([serialized], { type: "image/svg+xml" }));
    await new Promise((resolve, reject) => {
      image.onload = resolve;
      image.onerror = reject;
      image.src = imageUrl;
    });
    const canvas = document.createElement("canvas");
    canvas.width = width;
    canvas.height = height;
    const context = canvas.getContext("2d");
    context.fillStyle = PAPER;
    context.fillRect(0, 0, width, height);
    context.drawImage(image, 0, 0, width, height);
    return { svg: serialized, png: canvas.toDataURL("image/png").split(",")[1], width, height, render_ms: performance.now() - started };
  } catch (error) {
    return { error: "Mermaid 渲染失败，请重新选择文本或检查模型是否遵循 flowchart 格式。" };
  } finally {
    if (imageUrl) URL.revokeObjectURL(imageUrl);
    container.replaceChildren();
  }
};
