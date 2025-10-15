ARG DOCKER_LARAVEL_PHP_VERSION=8.4.100
ARG COMPOSER_VERSION=2.8.12

FROM lucidmodules/docker-laravel-php:${DOCKER_LARAVEL_PHP_VERSION}

ARG COMPOSER_VERSION
ENV COMPOSER_HOME=/tmp

RUN apt update -y \
    && apt install -y git unzip \
    && curl -sL https://getcomposer.org/installer -o - | php -- \
        --quiet \
        --install-dir=/usr/local/bin \
        --filename=composer \
        --version=$COMPOSER_VERSION \
    && apt clean -y \
    && rm -rf /var/lib/apt/lists/*

ENTRYPOINT ["composer"]
