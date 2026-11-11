import { createApp, h, onMounted, ref } from "vue";

import { api, ApiError, sendFile, type Session, type Video } from "./api";
import "./style.css";

function messageOf(error: unknown) {
  return error instanceof Error ? error.message : "Сервис недоступен.";
}

createApp({
  setup() {
    const session = ref<Session>();
    const videos = ref<Video[]>([]);
    const registering = ref(false);
    const busy = ref(false);
    const error = ref("");
    const status = ref("");
    const progress = ref<number>();
    const pending = ref<string>();

    async function loadVideos() {
      videos.value = (await api<{ videos: Video[] }>("/api/videos")).videos;
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
        if (session.value.username) await loadVideos();
      });
    }
    onMounted(refreshSession);

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
        await api("/api/uploads/" + pending.value + "/complete", { csrf });
      } catch (failure) {
        if (failure instanceof ApiError && failure.status < 500 && !failure.retryAfter) {
          pending.value = undefined;
          status.value = "";
        } else {
          status.value = "Проверку можно повторить";
        }
        throw failure;
      }
      pending.value = undefined;
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
      const csrf = session.value.csrf_token;
      await run(async () => {
        progress.value = 0;
        status.value = "Передаём файл";
        try {
          const upload = await api<{ id: string }>("/api/uploads", {
            csrf, data: { title: data.get("title"), size: file.size },
          });
          await sendFile(upload.id, file, csrf, (percent) => { progress.value = percent; });
          progress.value = 100;
          pending.value = upload.id;
          form.reset();
        } catch (failure) {
          status.value = "";
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
        videos.value = [];
        status.value = "";
        progress.value = undefined;
        pending.value = undefined;
      });
    }

    function credentials() {
      return h("section", { "data-testid": "auth-screen", "aria-labelledby": "auth-title" }, [
        h("h2", { id: "auth-title" }, registering.value ? "Регистрация" : "Вход"),
        h("form", { onSubmit: submitCredentials }, [
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
          h("h2", { id: "cabinet-title" }, "Кабинет · " + session.value?.username),
          h("button", { class: "secondary", disabled: busy.value, onClick: signOut }, "Выйти"),
        ]),
        h("form", { onSubmit: submitUpload }, [
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
            h("span", video.state === "QUEUED" ? "Ожидает обработки" : video.state),
          ])))
          : h("p", "Здесь появятся загруженные видео."),
      ]);
    }

    return () => h("main", [
      h("h1", "VidHarbor"),
      session.value?.username ? cabinet() : session.value ? credentials() : null,
      !session.value && !busy.value
        ? h("button", { onClick: refreshSession }, "Повторить подключение")
        : null,
      status.value ? h("p", { role: "status" }, status.value) : null,
      error.value ? h("p", { role: "alert" }, error.value) : null,
    ]);
  },
}).mount("#app");
