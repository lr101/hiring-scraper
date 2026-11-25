# Compose supplies the Dockerfiles. Fixed local tags let run.sh retag each
# verified image into its isolated project without rebuilding it.
group "default" {
  targets = ["quality", "app", "db"]
}

target "quality" {
  tags = ["hiring-scraper-checks:ci"]
  cache-from = ["type=gha,scope=hiring-scraper-checks"]
  cache-to = ["type=gha,mode=max,scope=hiring-scraper-checks,ignore-error=true,timeout=2m"]
}

target "app" {
  tags = ["hiring-scraper:ci"]
  cache-from = ["type=gha,scope=hiring-scraper-app"]
  cache-to = ["type=gha,mode=max,scope=hiring-scraper-app,ignore-error=true,timeout=2m"]
}

target "db" {
  tags = ["hiring-scraper-postgis-pgvector:ci"]
  cache-from = ["type=gha,scope=hiring-scraper-db"]
  cache-to = ["type=gha,mode=max,scope=hiring-scraper-db,ignore-error=true,timeout=2m"]
}
