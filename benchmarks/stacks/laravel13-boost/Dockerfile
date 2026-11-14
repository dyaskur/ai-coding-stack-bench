FROM composer:2.8.3 AS composer

FROM php:8.5.12-cli-bookworm

RUN apt-get update -qq \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
      git curl ca-certificates unzip libzip-dev libicu-dev \
 && docker-php-ext-install -j"$(nproc)" pdo_mysql zip intl bcmath pcntl \
 && rm -rf /var/lib/apt/lists/*

COPY --from=composer /usr/bin/composer /usr/local/bin/composer

ENV COMPOSER_NO_INTERACTION=1 \
    COMPOSER_ALLOW_SUPERUSER=0

WORKDIR /workspace
