FROM node:20-alpine AS builder

WORKDIR /app

COPY frontend-react/package.json frontend-react/package-lock.json* ./
RUN npm install

COPY frontend-react/ .
RUN rm -f .env .env.local .env.production
ARG VITE_API_BASE_URL=
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
RUN npm run build


FROM nginx:1.27-alpine AS runtime

COPY frontend-react/nginx.conf /etc/nginx/templates/default.conf.template
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80
