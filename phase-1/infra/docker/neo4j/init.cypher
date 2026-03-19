CREATE CONSTRAINT service_id_unique IF NOT EXISTS FOR (s:Service) REQUIRE s.id IS UNIQUE;
CREATE CONSTRAINT service_name_unique IF NOT EXISTS FOR (s:Service) REQUIRE s.name IS UNIQUE;
CREATE CONSTRAINT infra_id_unique IF NOT EXISTS FOR (i:Infra) REQUIRE i.id IS UNIQUE;
CREATE INDEX service_team_index IF NOT EXISTS FOR (s:Service) ON (s.team_id);
CREATE INDEX service_health_index IF NOT EXISTS FOR (s:Service) ON (s.health_status);
CREATE INDEX service_language_index IF NOT EXISTS FOR (s:Service) ON (s.language);
SHOW CONSTRAINTS;
