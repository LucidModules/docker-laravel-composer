ARG DOCKER_LARAVEL_PHP_VERSION=8.5
# Composer: the official binary image pinned by digest, plus the composer.phar sha256 published on
# getcomposer.org. CI (discover.py) resolves all three; these defaults are the reviewed floor.
ARG COMPOSER_VERSION=2.11.2
ARG COMPOSER_DIGEST=sha256:4eaf89f62eb9504807f2828f1829a71b7fcd202e89abe2f41c1fb2136068fbfb
ARG COMPOSER_SHA256=dd158c0afd41d15fac89e1b4e47b5d42911fe316c3687cd5edac98319b25d028

FROM composer/composer:${COMPOSER_VERSION}-bin@${COMPOSER_DIGEST} AS composer

FROM lucidmodules/docker-laravel-php:${DOCKER_LARAVEL_PHP_VERSION}

ARG COMPOSER_SHA256
ENV COMPOSER_HOME=/tmp

COPY --from=composer /composer /usr/local/bin/composer

RUN echo "${COMPOSER_SHA256}  /usr/local/bin/composer" | sha256sum -c - \
    && apt update -y \
    && apt install -y git unzip \
    && apt clean -y \
    && rm -rf /var/lib/apt/lists/*

ENTRYPOINT ["composer"]
