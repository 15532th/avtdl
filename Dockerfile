# Stage 1
FROM python:3.11-slim as build

RUN apt-get update
RUN apt-get install -y --no-install-recommends --no-install-suggests git

WORKDIR /build/
COPY ./ ./
RUN pip install --user .
RUN pip install --user yt-dlp

# Stage 2
FROM python:3.11-slim as app
RUN adduser avtdl

COPY --from=build  /root/.local /home/avtdl/.local
WORKDIR /home/avtdl/app
RUN chown avtdl -R /home/avtdl/

USER avtdl

ENV PATH=/home/avtdl/.local/bin:$PATH

EXPOSE 8080

CMD ["avtdl"]

LABEL org.opencontainers.image.source=https://github.com/15532th/avtdl
LABEL org.opencontainers.image.description="Monitoring and automation tool for Youtube and other streaming platforms"
LABEL org.opencontainers.image.licenses=MIT

