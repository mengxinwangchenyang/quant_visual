"use strict";

(() => {
  const switcher = document.querySelector(".page-switcher");
  if (!switcher) return;

  const currentFile = location.pathname.split("/").pop() || "daily_buys.html";
  const currentParams = new URLSearchParams(location.search);
  const groups = [
    { id: "daily", label: "每日", items: [["daily_buys.html", "买入"], ["daily_sells.html", "卖出"], ["daily_report.html", "简报"]] },
    { id: "weekly", label: "每周", items: [["weekly_buys.html", "买入"], ["weekly_sells.html", "卖出"], ["weekly_report.html", "简报"]] },
    { id: "cumulative", label: "累计", items: [["cumulative_buys.html", "买入"], ["cumulative_sells.html", "卖出"], ["cumulative_report.html", "简报"]] }
  ];
  const groupFor = (file) => file.startsWith("weekly_") ? "weekly" : file.startsWith("cumulative_") ? "cumulative" : "daily";
  const stateKey = { daily: "date", weekly: "week", cumulative: "end" };
  const currentGroup = groupFor(currentFile);

  function targetHref(file) {
    const target = new URL(file, location.href);
    const targetGroup = groupFor(file);
    if (targetGroup === currentGroup) {
      const value = currentParams.get(stateKey[targetGroup]);
      if (value) target.searchParams.set(stateKey[targetGroup], value);
      if (!file.endsWith("_report.html")) {
        const strategy = currentParams.get("strategy");
        if (strategy) target.searchParams.set("strategy", strategy);
      }
    }
    return `${target.pathname.split("/").pop()}${target.search}`;
  }

  const nav = document.createElement("nav");
  nav.className = "workspace-nav";
  nav.setAttribute("aria-label", "页面导航");
  nav.innerHTML = groups.map((group) => `<div class="workspace-nav-group">
    <span class="workspace-nav-scope">${group.label}</span>
    <span class="workspace-nav-links">${group.items.map(([file, label]) => `<a href="${targetHref(file)}"${file === currentFile ? ' class="active" aria-current="page"' : ""}>${label}</a>`).join("")}</span>
  </div>`).join("");
  switcher.replaceWith(nav);
})();
