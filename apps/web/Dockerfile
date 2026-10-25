FROM node:22-alpine
WORKDIR /app
COPY apps/web/package.json apps/web/package-lock.json* ./
RUN npm install
COPY apps/web .
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
RUN npm run build
CMD ["sh", "-c", "npx next start -H 0.0.0.0 -p ${PORT:-3000}"]
