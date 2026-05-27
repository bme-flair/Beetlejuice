#!/usr/bin/env Rscript

# Generate a static brightness graph for the web app.
# Usage:
#   Rscript scripts/plot_brightness.R data/observations.csv data/brightness_plot.svg Vis

args <- commandArgs(trailingOnly = TRUE)
input_path <- if (length(args) >= 1) args[[1]] else "data/observations.csv"
output_path <- if (length(args) >= 2) args[[2]] else "data/brightness_plot.svg"
summary_band <- if (length(args) >= 3) args[[3]] else "Vis"

if (!requireNamespace("ggplot2", quietly = TRUE)) {
  stop("The ggplot2 package is required. Install it with install.packages('ggplot2').", call. = FALSE)
}

normalize_band <- function(x) {
  toupper(sub("\\.$", "", trimws(as.character(x))))
}

make_empty_plot <- function(message) {
  ggplot2::ggplot() +
    ggplot2::annotate("text", x = 0, y = 0, label = message, size = 5) +
    ggplot2::theme_void() +
    ggplot2::labs(title = "Betelgeuse brightness")
}

if (!file.exists(input_path)) {
  plot <- make_empty_plot(sprintf("Missing input file: %s", input_path))
} else {
  observations <- read.csv(input_path, stringsAsFactors = FALSE)

  required_columns <- c("date_utc", "magnitude", "band")
  missing_columns <- setdiff(required_columns, names(observations))

  if (length(missing_columns) > 0) {
    plot <- make_empty_plot(sprintf("Missing columns: %s", paste(missing_columns, collapse = ", ")))
  } else {
    observations$magnitude <- suppressWarnings(as.numeric(observations$magnitude))
    observations$date <- as.Date(observations$date_utc)

    keep <- !is.na(observations$date) &
      !is.na(observations$magnitude) &
      normalize_band(observations$band) == normalize_band(summary_band)

    observations <- observations[keep, , drop = FALSE]

    if (nrow(observations) == 0) {
      plot <- make_empty_plot(sprintf("No %s-band observations yet", summary_band))
    } else {
      median_by_day <- aggregate(magnitude ~ date, data = observations, FUN = median)
      names(median_by_day) <- c("date", "median_magnitude")

      count_by_day <- aggregate(magnitude ~ date, data = observations, FUN = length)
      names(count_by_day) <- c("date", "observation_count")

      daily <- merge(median_by_day, count_by_day, by = "date", sort = TRUE)
      latest <- daily[nrow(daily), ]

      plot <- ggplot2::ggplot(daily, ggplot2::aes(x = date, y = median_magnitude)) +
        ggplot2::geom_line(linewidth = 0.5) +
        ggplot2::geom_point(ggplot2::aes(size = observation_count), alpha = 0.7) +
        ggplot2::scale_y_reverse() +
        ggplot2::scale_size_area(max_size = 4, guide = "none") +
        ggplot2::labs(
          title = "Betelgeuse brightness",
          subtitle = sprintf(
            "Daily median %s magnitude from AAVSO. Latest: %.3f on %s.",
            summary_band,
            latest$median_magnitude,
            latest$date
          ),
          x = NULL,
          y = sprintf("Median %s magnitude (lower is brighter)", summary_band),
          caption = "Source: AAVSO. Points are sized by daily observation count."
        ) +
        ggplot2::theme_minimal(base_size = 14) +
        ggplot2::theme(
          plot.title = ggplot2::element_text(face = "bold"),
          panel.grid.minor = ggplot2::element_blank(),
          plot.caption = ggplot2::element_text(hjust = 0)
        )
    }
  }
}

output_dir <- dirname(output_path)
if (!dir.exists(output_dir)) {
  dir.create(output_dir, recursive = TRUE)
}

ggplot2::ggsave(
  filename = output_path,
  plot = plot,
  width = 10,
  height = 5.5,
  units = "in",
  device = "svg"
)

message(sprintf("Wrote %s", output_path))
