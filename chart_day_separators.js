"use strict";

window.ChartDaySeparators = (() => {
  function attach(container, chart, points) {
    const starts = [];
    let previousDate = "";
    for (const point of points || []) {
      const date = String(point.date || "");
      if (previousDate && date && date !== previousDate) starts.push(point.time);
      if (date) previousDate = date;
    }
    if (!starts.length) return { destroy() {} };

    const layer = document.createElement("div");
    layer.className = "chart-day-separators";
    layer.setAttribute("aria-hidden", "true");
    const lines = starts.map(() => {
      const line = document.createElement("span");
      line.className = "chart-day-separator";
      layer.appendChild(line);
      return line;
    });
    container.appendChild(layer);

    let frame = 0;
    const update = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        const width = container.clientWidth;
        starts.forEach((time, index) => {
          const coordinate = chart.timeScale().timeToCoordinate(time);
          const visible = Number.isFinite(coordinate) && coordinate >= 0 && coordinate <= width;
          lines[index].hidden = !visible;
          if (visible) lines[index].style.transform = `translateX(${Math.round(coordinate - 1)}px)`;
        });
      });
    };

    const timeScale = chart.timeScale();
    timeScale.subscribeVisibleLogicalRangeChange(update);
    const resizeObserver = new ResizeObserver(update);
    resizeObserver.observe(container);
    requestAnimationFrame(update);

    return {
      destroy() {
        cancelAnimationFrame(frame);
        resizeObserver.disconnect();
        timeScale.unsubscribeVisibleLogicalRangeChange(update);
        layer.remove();
      }
    };
  }

  return { attach };
})();
