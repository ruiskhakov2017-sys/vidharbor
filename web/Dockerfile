FROM node:24-alpine@sha256:a34ab6d7f6f4813f4d0c5469d74685604b962fbdbf5793a7bc7b4998ecb867c8 AS build

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY index.html tsconfig.json ./
COPY src/ src/
COPY public/ public/
ARG VIDHARBOR_BASE_PATH=/
RUN npm run build -- --base "$VIDHARBOR_BASE_PATH"

FROM nginx:stable-alpine@sha256:583b7f2fce1cdb8da5ad5c39ebd3c33b98b87d493190e548bdee1fa8c619c311
RUN addgroup -g 65532 vidharbor && adduser -D -H -u 65532 -G vidharbor vidharbor \
    && sed -i -E 's/^user[[:space:]]+nginx;/user vidharbor;/' /etc/nginx/nginx.conf
COPY default.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist/ /usr/share/nginx/html/
