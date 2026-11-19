import { createApp, h, onBeforeUnmount, onMounted, ref } from "vue";

import { api, ApiError, sendFile, sitePath, sitePrefix, type Session, type Video } from "./api";
import { PublicVideos } from "./public-videos";
import "./style.css";
import { ThemePicker } from "./themes";
import "./themes.css";

function messageOf(error: unknown) {
  return error instanceof Error ? error.message : "Сервис недоступен.";
}

createApp({
  setup() {
    const path = window.location.pathname.slice(sitePrefix.length).replace(/\/$/, "");
    if (!path || path === "/catalog" || path.startsWith("/watch/")) {
      return () => h(PublicVideos, { videoId: path.startsWith("/watch/") ? path.slice(7) : "" });
    }
    const session = ref<Session>();
    const videos = ref<Video[]>([]);
    const registering = ref(false);
    const busy = ref(false);
    const error = ref("");
    const status = ref("");
    const progress = ref<number>();
    const pending = ref<string>();
    const pendingTrace = ref<string>();
    const editing = ref<string>();
    const editedTitle = ref("");
    // Keep the original failed job across an uncertain HTTP outcome, even if polls change it.
    const pendingRetries = ref<Record<string, string>>({});

    let videoListRequest = 0;

    async function loadVideos() {
      const request = ++videoListRequest;
      const result = await api<{ videos: Video[] }>("/api/videos");
      if (request === videoListRequest) videos.value = result.videos;
    }

    async function run(action: () => Promise<void>) {
      error.value = "";
      busy.value = true;
      try {
        await action();
      } catch (failure) {
        error.value = messageOf(failure);
      } finally {
        busy.value = false;
      }
    }

    function refreshSession() {
      return run(async () => {
        session.value = await api<Session>("/api/auth/session");
        if (session.value.username && !(path === "/account" && session.value.guest)) await loadVideos();
      });
    }
    onMounted(refreshSession);
    const statusPoll = window.setInterval(() => {
      if (session.value?.username && !busy.value &&
          videos.value.some((video) => ["QUEUED", "PROCESSING", "RETRY_WAIT", "DELETING"].includes(video.state))) {
        void loadVideos().catch(() => { /* Retry on the next poll. */ });
      }
    }, 3000);
    onBeforeUnmount(() => window.clearInterval(statusPoll));

    async function submitCredentials(event: Event) {
      event.preventDefault();
      if (!session.value) return;
      const form = event.currentTarget as HTMLFormElement;
      const data = Object.fromEntries(new FormData(form));
      const csrf = session.value.csrf_token;
      await run(async () => {
        status.value = "";
        if (registering.value) {
          await api("/api/auth/register", { csrf, data });
          status.value = "Аккаунт создан. Теперь войдите.";
          registering.value = false;
        } else {
          session.value = await api<Session>("/api/auth/login", { csrf, data });
          await loadVideos();
        }
        form.reset();
      });
    }

    async function completeUpload(csrf: string) {
      if (!pending.value) return;
      status.value = "Проверяем файл";
      try {
        await api("/api/uploads/" + pending.value + "/complete", { csrf, traceparent: pendingTrace.value });
      } catch (failure) {
        if (failure instanceof ApiError && failure.status < 500 && !failure.retryAfter) {
          pending.value = undefined;
          pendingTrace.value = undefined;
          status.value = "";
        } else {
          status.value = "Проверку можно повторить";
        }
        throw failure;
      }
      pending.value = undefined;
      pendingTrace.value = undefined;
      status.value = "Видео принято и ожидает обработки";
    }

    async function submitUpload(event: Event) {
      event.preventDefault();
      if (!session.value || pending.value) return;
      const form = event.currentTarget as HTMLFormElement;
      const data = new FormData(form);
      const file = data.get("file");
      if (!(file instanceof File) || !file.size) {
        error.value = "Выберите непустой файл.";
        return;
      }
      const current = session.value;
      await run(async () => {
        if (!current.username && current.demo_studio) {
          session.value = await api<Session>("/api/auth/guest", { csrf: current.csrf_token });
        }
        const csrf = session.value!.csrf_token;
        progress.value = 0;
        status.value = "Передаём файл";
        try {
          const upload = await api<{ id: string }>("/api/uploads", {
            csrf, data: { title: data.get("title"), size: file.size },
          });
          pendingTrace.value = await sendFile(upload.id, file, csrf, (percent) => { progress.value = percent; });
          progress.value = 100;
          pending.value = upload.id;
          form.reset();
        } catch (failure) {
          status.value = "";
          pendingTrace.value = undefined;
          progress.value = undefined;
          throw failure;
        }
        await completeUpload(csrf);
        await loadVideos();
      });
    }

    function retryCompletion() {
      const csrf = session.value?.csrf_token;
      if (!csrf) return;
      return run(async () => {
        await completeUpload(csrf);
        await loadVideos();
      });
    }

    function signOut() {
      const csrf = session.value?.csrf_token;
      if (!csrf) return;
      return run(async () => {
        session.value = await api<Session>("/api/auth/logout", { csrf });
        ++videoListRequest;
        videos.value = [];
        status.value = "";
        progress.value = undefined;
        pending.value = undefined;
        pendingTrace.value = undefined;
        editing.value = undefined;
        editedTitle.value = "";
        pendingRetries.value = {};
      });
    }

    function startRename(video: Video) {
      editing.value = video.id;
      editedTitle.value = video.title;
      error.value = "";
      status.value = "";
    }

    function deleteVideo(video: Video) {
      const csrf = session.value?.csrf_token;
      if (!csrf || busy.value || !window.confirm("Удалить видео и его файлы? Ссылки перестанут работать.")) return;
      return run(async () => {
        const result = await api<{ state: string }>("/api/videos/" + video.id + "/delete", { csrf });
        ++videoListRequest;
        videos.value = result.state === "DELETED"
          ? videos.value.filter((item) => item.id !== video.id)
          : videos.value.map((item) => item.id === video.id ? { ...item, state: result.state } : item);
        editing.value = undefined;
        delete pendingRetries.value[video.id];
        status.value = result.state === "DELETED" ? "Видео удалено" : "Доступ закрыт. Файлы удаляются.";
      });
    }

    function retryProcessing(video: Video) {
      const csrf = session.value?.csrf_token;
      const failedJob = pendingRetries.value[video.id] ?? video.job_id;
      if (!csrf || !failedJob || busy.value) return;
      pendingRetries.value[video.id] = failedJob;
      return run(async () => {
        status.value = "";
        try {
          const result = await api<{ video: Video }>(
            "/api/videos/" + video.id + "/jobs/" + failedJob + "/retry", { csrf },
          );
          ++videoListRequest;
          videos.value = videos.value.map((item) => item.id === video.id ? result.video : item);
          delete pendingRetries.value[video.id];
          status.value = "Запрос повтора принят";
        } catch (failure) {
          if (failure instanceof ApiError && failure.status >= 400 && failure.status < 500 && !failure.retryAfter) {
            delete pendingRetries.value[video.id];
          }
          if (!(failure instanceof ApiError)) {
            throw new Error("Ответ сервиса не получен. Нажмите «Повторить запрос», чтобы узнать результат.");
          }
          throw failure;
        }
      });
    }

    function submitRename(event: Event) {
      event.preventDefault();
      const csrf = session.value?.csrf_token;
      const id = editing.value;
      if (!csrf || !id || busy.value) return;
      return run(async () => {
        const updated = await api<Video>("/api/videos/" + id + "/rename", {
          csrf, data: { title: editedTitle.value },
        });
        // Earlier polls may still be in flight with the title from before the rename.
        ++videoListRequest;
        videos.value = videos.value.map((video) => video.id === id ? updated : video);
        editing.value = undefined;
        status.value = "Название сохранено";
      });
    }

    function credentials() {
      return h("section", { "data-testid": "auth-screen", "aria-labelledby": "auth-title" }, [
        h("h2", { id: "auth-title" }, registering.value ? "Регистрация" : "Вход"),
        h("form", { key: "credentials", onSubmit: submitCredentials }, [
          h("label", ["Имя пользователя", h("input", {
            name: "username", autocomplete: "username", required: true, maxlength: 150,
          })]),
          h("label", ["Пароль", h("input", {
            name: "password", type: "password", required: true,
            autocomplete: registering.value ? "new-password" : "current-password",
          })]),
          h("div", { class: "actions" }, [
            h("button", { type: "submit", disabled: busy.value }, registering.value ? "Зарегистрироваться" : "Войти"),
            h("button", {
              type: "button", class: "secondary", disabled: busy.value,
              onClick: () => {
                registering.value = !registering.value;
                error.value = "";
                status.value = "";
              },
            }, registering.value ? "Ко входу" : "Создать аккаунт"),
          ]),
        ]),
      ]);
    }

    function cabinet() {
      return h("section", { "aria-labelledby": "cabinet-title" }, [
        h("div", { class: "cabinet-header" }, [
          h("h2", { id: "cabinet-title" }, session.value?.guest || !session.value?.username
            ? "Загрузить видео" : "Кабинет · " + session.value.username),
          session.value?.username && !session.value.guest
            ? h("button", { class: "secondary", disabled: busy.value, onClick: signOut }, "Выйти") : null,
        ]),
        session.value?.demo_studio && (!session.value.username || session.value.guest)
          ? h("p", { class: "guest-note" }, "Управление вашими загрузками доступно в этом браузере, пока сохранены cookies студии.") : null,
        h("form", { key: "upload", onSubmit: submitUpload }, [
          h("label", ["Название видео", h("input", { name: "title", required: true, maxlength: 200 })]),
          h("label", ["Видеофайл", h("input", {
            name: "file", type: "file", accept: ".mp4,.mov,.mkv,.webm", required: true,
          })]),
          h("div", { class: "actions" }, [
            h("button", { type: "submit", disabled: busy.value || !!pending.value }, "Загрузить"),
            pending.value && !busy.value
              ? h("button", { type: "button", onClick: retryCompletion }, "Повторить проверку")
              : null,
          ]),
          progress.value === undefined ? null : h("div", { class: "progress" }, [
            h("progress", { value: progress.value, max: 100, "aria-label": "Прогресс загрузки" }),
            h("output", progress.value + " %"),
          ]),
        ]),
        h("h3", "Мои видео"),
        videos.value.length
          ? h("ul", { class: "videos" }, videos.value.map((video) => h("li", { key: video.id }, [
            h("strong", video.title),
            video.state === "READY"
              ? h("a", { href: video.watch_url }, "Готово · Смотреть")
              : h("span", video.state === "FAILED" ? "Не удалось обработать"
                : video.state === "DELETING" ? "Удаляется" : "Обрабатывается"),
            video.state === "FAILED" && video.retry_advice ? h("p", video.retry_advice) : null,
            video.can_retry || pendingRetries.value[video.id]
              ? h("button", {
                type: "button", disabled: busy.value, onClick: () => retryProcessing(video),
              }, pendingRetries.value[video.id] ? "Повторить запрос" : "Повторить обработку")
              : null,
            editing.value === video.id
              ? h("form", { onSubmit: submitRename, "aria-label": "Переименование видео" }, [
                h("label", ["Новое название", h("input", {
                  name: "title", required: true, maxlength: 200, value: editedTitle.value,
                  disabled: busy.value,
                  onInput: (event: Event) => { editedTitle.value = (event.target as HTMLInputElement).value; },
                })]),
                h("div", { class: "actions" }, [
                  h("button", { type: "submit", disabled: busy.value }, "Сохранить"),
                  h("button", {
                    type: "button", class: "secondary", disabled: busy.value,
                    onClick: () => { editing.value = undefined; error.value = ""; },
                  }, "Отмена"),
                ]),
              ])
              : h("button", {
                type: "button", class: "secondary", disabled: busy.value || video.state === "DELETING",
                onClick: () => startRename(video),
              }, "Переименовать"),
            h("button", { type: "button", disabled: busy.value || video.state === "DELETING",
              onClick: () => deleteVideo(video) }, "Удалить"),
          ])))
          : h("p", "Здесь появятся загруженные видео."),
      ]);
    }

    function studioContent() {
      const current = session.value;
      if (!current) return null;
      if (path === "/account" && current.guest) return credentials();
      if (current.username || (current.demo_studio && path !== "/account")) return cabinet();
      return credentials();
    }

    return () => h("main", { class: "studio" }, [
      h("nav", { class: "studio-nav" }, [h("a", { href: sitePath("/") }, "← Каталог"), h(ThemePicker)]),
      h("h1", "Творческая студия"),
      studioContent(),
      !session.value && !busy.value
        ? h("button", { onClick: refreshSession }, "Повторить подключение")
        : null,
      status.value ? h("p", { role: "status" }, status.value) : null,
      error.value ? h("p", { role: "alert" }, error.value) : null,
    ]);
  },
}).mount("#app");
