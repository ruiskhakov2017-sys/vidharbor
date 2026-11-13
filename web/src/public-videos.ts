import type Hls from "hls.js";
import { defineComponent, h, onBeforeUnmount, onMounted, ref } from "vue";

import { api, ApiError } from "./api";

type PublicVideo = { id: string; title: string; watch_url: string; playlist_url: string };

const Player = defineComponent({
  props: { source: { type: String, required: true } },
  setup(props) {
    const element = ref<HTMLVideoElement>();
    const levels = ref<string[]>([]);
    const selection = ref(-1);
    const active = ref(-1);
    const error = ref("");
    let engine: Hls | undefined;
    let disposed = false;

    onMounted(async () => {
      try {
        const { default: Engine } = await import("hls.js");
        if (disposed || !element.value) return;
        if (!Engine.isSupported()) {
          error.value = "В этом браузере HLS недоступен.";
          return;
        }
        engine = new Engine();
        engine.on(Engine.Events.MANIFEST_PARSED, (_, data) => {
          levels.value = data.levels.map(({ width, height }) => {
            const shortSide = Math.min(width, height);
            const dimensions = width + "×" + height;
            return [360, 480, 720].includes(shortSide) ? shortSide + "p · " + dimensions : dimensions;
          });
        });
        engine.on(Engine.Events.LEVEL_SWITCHED, (_, data) => {
          active.value = data.level;
        });
        engine.on(Engine.Events.ERROR, (_, data) => {
          if (data.fatal) error.value = "Не удалось воспроизвести видео. Обновите страницу.";
        });
        engine.on(Engine.Events.MEDIA_ATTACHED, () => engine?.loadSource(props.source));
        engine.attachMedia(element.value);
      } catch {
        error.value = "Не удалось загрузить плеер.";
      }
    });
    onBeforeUnmount(() => {
      disposed = true;
      engine?.destroy();
    });

    function changeQuality(event: Event) {
      const select = event.target as HTMLSelectElement;
      selection.value = Number(select.value);
      if (engine) engine.currentLevel = selection.value;
    }

    return () => h("div", [
      h("video", {
        class: "player", ref: element, controls: true, playsinline: true, preload: "metadata",
        "aria-label": "Видеоплеер", "data-active-level": active.value,
      }),
      levels.value.length ? h("label", { class: "quality" }, [
        "Качество",
        h("select", { value: selection.value, onChange: changeQuality }, [
          h("option", { value: -1 }, "Авто"),
          ...levels.value.map((label, index) => h("option", { value: index, key: index }, label)),
        ]),
      ]) : null,
      error.value ? h("p", { role: "alert" }, error.value) : null,
    ]);
  },
});

export const PublicVideos = defineComponent({
  props: { videoId: { type: String, required: true } },
  setup(props) {
    const items = ref<PublicVideo[]>([]);
    const video = ref<PublicVideo>();
    const query = ref(new URLSearchParams(window.location.search).get("q") ?? "");
    const busy = ref(true);
    const error = ref("");

    async function load() {
      busy.value = true;
      error.value = "";
      try {
        if (props.videoId) {
          video.value = await api<PublicVideo>("/api/catalog/" + encodeURIComponent(props.videoId));
        } else {
          const params = new URLSearchParams({ q: query.value });
          items.value = (await api<{ videos: PublicVideo[] }>("/api/catalog?" + params)).videos;
        }
      } catch (failure) {
        error.value = failure instanceof ApiError && failure.status === 404
          ? "Видео недоступно."
          : "Не удалось загрузить данные. Повторите запрос.";
      } finally {
        busy.value = false;
      }
    }

    onMounted(load);

    return () => h("main", [
      h("h1", "VidHarbor"),
      h("nav", { class: "nav" }, [
        props.videoId ? h("a", { href: "/catalog" }, "Каталог") : null,
        h("a", { href: "/" }, "Кабинет"),
      ]),
      props.videoId
        ? video.value && h("section", [h("h2", video.value.title), h(Player, { source: video.value.playlist_url })])
        : h("section", [
            h("h2", "Каталог"),
            h("form", { onSubmit: (event: Event) => { event.preventDefault(); void load(); } }, [
              h("label", ["Название видео", h("input", {
                type: "search", value: query.value, maxlength: 200,
                onInput: (event: Event) => { query.value = (event.target as HTMLInputElement).value; },
              })]),
              h("div", [h("button", { disabled: busy.value }, "Найти")]),
            ]),
            !busy.value && !error.value
              ? items.value.length
                ? h("ul", { class: "videos" }, items.value.map((item) =>
                    h("li", { key: item.id }, [h("a", { href: item.watch_url }, item.title)])))
                : h("p", "Видео не найдены.")
              : null,
          ]),
      busy.value ? h("p", { role: "status" }, "Загружаем…") : null,
      error.value ? h("p", { role: "alert" }, error.value) : null,
    ]);
  },
});
