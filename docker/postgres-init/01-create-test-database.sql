-- Runs once, when the PostgreSQL volume is first created (see docker-compose.yml).
-- A separate database for pytest: the tests drop and recreate every table in it, so they must
-- never point at the dev database "jiak".
CREATE DATABASE jiak_test;
