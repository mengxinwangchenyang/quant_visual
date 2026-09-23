module.exports = { // 导出 Tailwind 构建配置对象。
  content: ["./visual/index.html", "./visual/app.js"], // 扫描可视化页面和脚本中的工具类。
  theme: { // 定义 Tailwind 主题配置。
    extend: {}, // 保留默认主题扩展入口。
  }, // 结束主题配置。
  plugins: [], // 当前页面暂不使用 Tailwind 插件。
}; // 结束 Tailwind 配置导出。
