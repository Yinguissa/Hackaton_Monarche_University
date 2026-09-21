FROM nginx:1.27-alpine

# The frontend is a single self-contained HTML file (React + Babel +
# Chart.js all loaded from CDN, per the locked frontend stack) — no Node
# build step required. nginx just serves it as a static file.
COPY index.html /usr/share/nginx/html/index.html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
