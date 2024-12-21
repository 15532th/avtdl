FROM jrottenberg/ffmpeg:7.1-ubuntu as base

RUN userdel -f ubuntu
RUN groupadd -f -g 1000 avtdl
RUN useradd -m -u 1000 -g 1000 -o avtdl
RUN apt-get update
RUN apt-get install -y --no-install-recommends --no-install-suggests python3 ca-certificates
ENV PATH=/home/avtdl/.local/bin:$PATH

# Stage 1
FROM base as build

RUN apt-get install -y --no-install-recommends --no-install-suggests git pipx wget unzip

WORKDIR /home/avtdl/build
COPY ./ ./
RUN chown 1000:1000 -R /home/avtdl

USER avtdl
RUN pipx install .
RUN pipx install yt-dlp

RUN wget "https://github.com/Kethsar/ytarchive/releases/download/latest/ytarchive_linux_amd64.zip" -O ytarchive.zip
RUN unzip ytarchive.zip -d /home/avtdl/.local/bin/

# Stage 2
FROM base as app

WORKDIR /home/avtdl/app

COPY --from=build  /home/avtdl/.local /home/avtdl/.local
RUN chown 1000:1000 -R /home/avtdl

USER avtdl

EXPOSE 8080

ENTRYPOINT ["avtdl"]
CMD ["--host", "0.0.0.0"]

LABEL org.opencontainers.image.source=https://github.com/15532th/avtdl
LABEL org.opencontainers.image.description="Monitoring and automation tool for Youtube and other streaming platforms \
\
Includes additional tools commonly used with avtdl: ffmpeg, yt-dlp and ytarchive."
LABEL org.opencontainers.image.licenses=MIT