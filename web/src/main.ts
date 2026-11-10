import { createApp, h } from "vue";
import "./style.css";

createApp({
  render: () =>
    h("main", { "aria-labelledby": "title" }, [
      h("p", { class: "stage" }, "S01 · Локальный запуск"),
      h("h1", { id: "title" }, "VidHarbor"),
      h("p", { "data-testid": "s01-placeholder" }, "Основа VidHarbor запущена."),
      h("nav", { "aria-label": "Проверки сервиса" }, [
        h("a", { href: "/health/live" }, "Проверить запуск"),
        h("a", { href: "/health/ready" }, "Проверить готовность"),
      ]),
    ]),
}).mount("#app");
