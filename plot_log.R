# Plot an LS430 drive log made by live.py
# Usage: Rscript plot_log.R logs/drive_YYYYMMDD_HHMMSS.csv
# Needs: install.packages(c("ggplot2", "tidyr"))
suppressPackageStartupMessages({ library(ggplot2); library(tidyr) })

args <- commandArgs(trailingOnly = TRUE)
f <- if (length(args)) args[1] else {
  logs <- list.files("logs", pattern = "\\.csv$", full.names = TRUE)
  logs[which.max(file.mtime(logs))]
}
d <- read.csv(f)
long <- pivot_longer(d, -time_s, names_to = "signal", values_to = "value")

p <- ggplot(long, aes(time_s, value)) +
  geom_line(colour = "steelblue") +
  facet_wrap(~signal, scales = "free_y", ncol = 2) +
  labs(x = "Time (s)", y = NULL, title = basename(f)) +
  theme_minimal()

out <- sub("\\.csv$", ".png", f)
ggsave(out, p, width = 9, height = 10, dpi = 120)
cat("Saved", out, "\n")
print(summary(d[-1]))
