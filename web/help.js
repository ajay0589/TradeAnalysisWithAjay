let helpGuideData = null;
let helpGuideLoading = false;

function helpElement(tag, text) {
  const element = document.createElement(tag);
  if (text !== undefined) element.textContent = text;
  return element;
}

async function loadHelpGuide() {
  if (helpGuideData || helpGuideLoading) return;
  helpGuideLoading = true;
  $("helpStatus").textContent = "Loading guide...";
  $("helpRetry").hidden = true;
  try {
    const response = await fetch("/api/help");
    if (!response.ok) throw new Error("Guide could not be loaded");
    const data = await response.json();
    renderHelpGuide(data);
    helpGuideData = data;
    $("helpDownload").disabled = false;
    $("helpReviewDownload").disabled = false;
    $("helpStatus").textContent = "Paper observations, technical factors and validation limits";
  } catch (error) {
    $("helpStatus").textContent = `Unable to load help: ${error.message}`;
    $("helpRetry").hidden = false;
  } finally {
    helpGuideLoading = false;
  }
}

function renderHelpGuide(data) {
  $("helpVersion").textContent = `Reviewed ${data.reviewed_on} | ${data.lab_version}`;
  $("helpSections").replaceChildren();
  $("helpContents").replaceChildren();
  data.sections.forEach((section, index) => {
    const details = helpElement("details");
    details.id = `help-${section.id}`;
    details.className = "help-topic";
    details.open = index === 0;
    details.appendChild(helpElement("summary", section.title));
    const definitions = helpElement("dl");
    section.items.forEach(([term, explanation]) => {
      definitions.appendChild(helpElement("dt", term));
      definitions.appendChild(helpElement("dd", explanation));
    });
    details.appendChild(definitions);
    $("helpSections").appendChild(details);
    const link = helpElement("a", section.title);
    link.href = `#${details.id}`;
    link.addEventListener("click", () => { details.open = true; });
    $("helpContents").appendChild(link);
  });
  $("helpSources").replaceChildren(helpElement("h3", "References"));
  data.sources.forEach((source) => {
    const link = helpElement("a", source.title);
    link.href = source.url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    $("helpSources").appendChild(link);
  });
}

function downloadHelpText(text, name) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/plain;charset=utf-8" }));
  const anchor = helpElement("a");
  anchor.href = url;
  anchor.download = name;
  anchor.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

$("helpRetry").addEventListener("click", loadHelpGuide);
$("helpReviewDownload").addEventListener("click", () => {
  if (helpGuideData) downloadHelpText(helpGuideData.review_template, "trading-session-review.txt");
});
$("helpDownload").addEventListener("click", () => {
  if (!helpGuideData) return;
  const lines = ["Trading Desk Help & About", `Reviewed ${helpGuideData.reviewed_on} | ${helpGuideData.lab_version}`];
  helpGuideData.sections.forEach((section) => {
    lines.push("", section.title);
    section.items.forEach(([term, explanation]) => lines.push("", term, explanation));
  });
  lines.push("", "References", ...helpGuideData.sources.map((source) => `${source.title}: ${source.url}`));
  downloadHelpText(lines.join("\n"), "trading-desk-guide.txt");
});
