import { useEffect } from "react";

/**
 * 轻量滚动浮现监听：
 * 监听页面内所有 .hk-reveal 元素，进入视口一次即添加 .is-visible 并取消监听。
 * 同时通过 MutationObserver 自动纳管异步接口渲染的新区块（如角色卡片、曲库卡片、任务队列）。
 */
export function useScrollReveal(routeKey: string) {
  useEffect(() => {
    const observeAll = (io: IntersectionObserver) => {
      const nodes = document.querySelectorAll<HTMLElement>(".hk-reveal:not(.is-visible)");
      nodes.forEach((el) => io.observe(el));
    };

    if (typeof IntersectionObserver === "undefined") {
      document
        .querySelectorAll<HTMLElement>(".hk-reveal")
        .forEach((el) => el.classList.add("is-visible"));
      return;
    }

    const io = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            entry.target.classList.add("is-visible");
            io.unobserve(entry.target);
          }
        }
      },
      {
        threshold: 0.06,
        rootMargin: "0px 0px -24px 0px",
      }
    );

    // 首帧开始观察当前路由节点
    const rafId = window.requestAnimationFrame(() => observeAll(io));

    const mo = new MutationObserver(() => {
      observeAll(io);
    });
    const mainEl = document.querySelector("main") ?? document.body;
    mo.observe(mainEl, { childList: true, subtree: true });

    return () => {
      window.cancelAnimationFrame(rafId);
      mo.disconnect();
      io.disconnect();
    };
  }, [routeKey]);
}
