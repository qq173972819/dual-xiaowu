// 移动端导航开合已由内联 onclick 处理；这里仅做导航高亮与平滑锚点。
(function () {
  var links = document.querySelectorAll(".nav a");
  var path = location.pathname.split("/").pop() || "index.html";
  links.forEach(function (a) {
    var href = a.getAttribute("href");
    if (href === path) a.classList.add("active");
  });
  // 点击导航后收起移动端菜单
  document.getElementById("nav").addEventListener("click", function (e) {
    if (e.target.tagName === "A") document.getElementById("nav").classList.remove("open");
  });
})();
