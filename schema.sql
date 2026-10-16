CREATE TABLE `users` (
  `id` int NOT NULL AUTO_INCREMENT,
  `name` varchar(16) NOT NULL,
  `safe_name` varchar(16) NOT NULL,
  `token` char(32) NOT NULL,
  `priv` int NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `users_name_uindex` (`name`),
  UNIQUE KEY `users_safe_name_uindex` (`safe_name`),
  UNIQUE KEY `users_token_uindex` (`token`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE `uploads` (
  `id` int NOT NULL AUTO_INCREMENT,
  `name` varchar(32) NOT NULL COMMENT 'unsure how long extension length could become',
  `user_id` int NOT NULL,
  `when` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `size` bigint unsigned NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uploads_name_uindex` (`name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
