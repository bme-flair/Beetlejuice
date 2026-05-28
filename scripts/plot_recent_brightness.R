#!/usr/bin/env Rscript

# Generate a recent-focus brightness graph and table data for the web app.
# Usage:
#   Rscript scripts/plot_recent_brightness.R data/observations.csv data/recent_brightness_plot.svg data/recent_brightness.json Vis 420 21

args <- commandArgs(trailingOnly = TRUE)
input_path <- if (length(args) >= 1) args[[1]] else "data/observations.csv"
plot_output_path <- if (length(args) >= 2) args[[2]] else "data/recent_brightness_plot.svg"
table_output_path <- if (length(args) >= 3) args[[3]] else "data/recent_brightness.json"
summary_band <- if (length(args) >= 4) args[[4]] else "Vis"
cycle_period_days <- if (length(args) >= 5) as.numeric(args[[5]]) else 420
phase_window_days <- if (length(args) >= 6) as.numeric(args[[6]]) else 21
recent_plot_days <- 30
recent_table_days <- 10
exclude_recent_reference_days <- 60

if (!requireNamespace("ggplot2", quietly = TRUE)) {
  stop("The ggplot2 package is required. Install it with install.packages('ggplot2').", call. = FALSE)
}

if (!requireNamespace("svglite", quietly = TRUE)) {
  stop("The svglite package is required to save SVG output. Install it with install.packages('svglite').", call. = FALSE)
}

normalize_band <- function(x) {
  toupper(sub("\\.$", "", trimws(as.character(x))))
}

json_escape <- function(x) {
  x <- as.character(x)
  x <- gsub("\\\\", "\\\\\\\\", x)
  x <- gsub('"', '\\\\"', x)
  x <- gsub("\n", "\\\\n", x)
  x
}

json_value <- function(x, digits = 4) {
  if (length(x) == 0 || is.na(x)) {
    return("null")
  }
  if (is.numeric(x)) {
    return(format(round(x, digits), trim = TRUE, scientific = FALSE))
  }
  if (inherits(x, "Date")) {
    return(sprintf('"%s"', format(x, "%Y-%m-%d")))
  }
  sprintf('"%s"', json_escape(x))
}

make_empty_plot <- function(message) {
  ggplot2::ggplot() +
    ggplot2::annotate("text", x = 0, y = 0, label = message, size = 5) +
    ggplot2::theme_void() +
    ggplot2::labs(title = "Recent Betelgeuse brightness")
}

cycle_reference_for_date <- function(target_date, daily, period_days, phase_window, exclude_after) {
  historical <- daily[daily$date <= exclude_after, , drop = FALSE]
  if (nrow(historical) == 0) {
    return(list(median = NA_real_, n = 0))
  }

  delta_days <- as.numeric(target_date - historical$date)
  phase_distance <- abs(delta_days - round(delta_days / period_days) * period_days)
  matches <- historical[phase_distance <= phase_window, , drop = FALSE]

  if (nrow(matches) == 0) {
    return(list(median = NA_real_, n = 0))
  }

  list(
    median = median(matches$median_magnitude, na.rm = TRUE),
    n = nrow(matches)
  )
}

if (!file.exists(input_path)) {
  plot <- make_empty_plot(sprintf("Missing input file: %s", input_path))
  recent_table <- data.frame()
  latest_data_date <- NA
} else {
  observations <- read.csv(input_path, stringsAsFactors = FALSE)
  required_columns <- c("date_utc", "magnitude", "band")
  missing_columns <- setdiff(required_columns, names(observations))

  if (length(missing_columns) > 0) {
    plot <- make_empty_plot(sprintf("Missing columns: %s", paste(missing_columns, collapse = ", ")))
    recent_table <- data.frame()
    latest_data_date <- NA
  } else {
    observations$magnitude <- suppressWarnings(as.numeric(observations$magnitude))
    observations$date <- as.Date(observations$date_utc)

    keep <- !is.na(observations$date) &
      !is.na(observations$magnitude) &
      normalize_band(observations$band) == normalize_band(summary_band)

    observations <- observations[keep, , drop = FALSE]

    if (nrow(observations) == 0) {
      plot <- make_empty_plot(sprintf("No %s-band observations yet", summary_band))
      recent_table <- data.frame()
      latest_data_date <- NA
    } else {
      median_by_day <- aggregate(magnitude ~ date, data = observations, FUN = median)
      names(median_by_day) <- c("date", "median_magnitude")

      count_by_day <- aggregate(magnitude ~ date, data = observations, FUN = length)
      names(count_by_day) <- c("date", "observation_count")

      daily <- merge(median_by_day, count_by_day, by = "date", sort = TRUE)
      latest_data_date <- max(daily$date)
      reference_exclude_after <- latest_data_date - exclude_recent_reference_days

      recent_dates <- seq(latest_data_date - (recent_plot_days - 1), latest_data_date, by = "day")
      recent <- merge(data.frame(date = recent_dates), daily, by = "date", all.x = TRUE, sort = TRUE)

      refs <- lapply(
        recent$date,
        cycle_reference_for_date,
        daily = daily,
        period_days = cycle_period_days,
        phase_window = phase_window_days,
        exclude_after = reference_exclude_after
      )
      recent$cycle_reference_magnitude <- vapply(refs, function(x) x$median, numeric(1))
      recent$cycle_reference_count <- vapply(refs, function(x) x$n, numeric(1))

      actual_recent <- recent[!is.na(recent$median_magnitude), , drop = FALSE]
      ref_recent <- recent[!is.na(recent$cycle_reference_magnitude), , drop = FALSE]

      plot <- ggplot2::ggplot() +
        ggplot2::geom_line(
          data = ref_recent,
          ggplot2::aes(x = date, y = cycle_reference_magnitude),
          linewidth = 0.7,
          linetype = "dashed",
          alpha = 0.75
        ) +
        ggplot2::geom_line(
          data = actual_recent,
          ggplot2::aes(x = date, y = median_magnitude),
          linewidth = 0.8
        ) +
        ggplot2::geom_point(
          data = actual_recent,
          ggplot2::aes(x = date, y = median_magnitude, size = observation_count),
          alpha = 0.8
        ) +
        ggplot2::scale_y_reverse() +
        ggplot2::scale_size_area(max_size = 4, guide = "none") +
        ggplot2::labs(
          title = "Recent Betelgeuse brightness",
          subtitle = sprintf(
            "Last %s days vs. same phase of an approximate %s-day cycle",
            recent_plot_days,
            cycle_period_days
          ),
          x = NULL,
          y = sprintf("Median %s magnitude (lower is brighter)", summary_band),
          caption = sprintf(
            "Solid = recent daily median. Dashed = historical cycle-phase median within +/- %s days; reference excludes the latest %s days.",
            phase_window_days,
            exclude_recent_reference_days
          )
        ) +
        ggplot2::theme_minimal(base_size = 14) +
        ggplot2::theme(
          plot.title = ggplot2::element_text(face = "bold"),
          panel.grid.minor = ggplot2::element_blank(),
          plot.caption = ggplot2::element_text(hjust = 0)
        )

      table_dates <- seq(latest_data_date - (recent_table_days - 1), latest_data_date, by = "day")
      recent_table <- merge(data.frame(date = table_dates), daily, by = "date", all.x = TRUE, sort = TRUE)

      table_refs <- lapply(
        recent_table$date,
        cycle_reference_for_date,
        daily = daily,
        period_days = cycle_period_days,
        phase_window = phase_window_days,
        exclude_after = reference_exclude_after
      )
      recent_table$cycle_reference_magnitude <- vapply(table_refs, function(x) x$median, numeric(1))
      recent_table$cycle_reference_count <- vapply(table_refs, function(x) x$n, numeric(1))
      recent_table$delta_from_reference <- recent_table$median_magnitude - recent_table$cycle_reference_magnitude
      recent_table$observation_count[is.na(recent_table$observation_count)] <- 0
    }
  }
}

for (path in c(plot_output_path, table_output_path)) {
  output_dir <- dirname(path)
  if (!dir.exists(output_dir)) {
    dir.create(output_dir, recursive = TRUE)
  }
}

ggplot2::ggsave(
  filename = plot_output_path,
  plot = plot,
  width = 10,
  height = 5.5,
  units = "in",
  device = svglite::svglite
)

metadata <- c(
  "{",
  sprintf('  "metadata": {'),
  sprintf('    "summary_band": %s,', json_value(summary_band)),
  sprintf('    "latest_data_date": %s,', json_value(latest_data_date)),
  sprintf('    "cycle_period_days": %s,', json_value(cycle_period_days)),
  sprintf('    "phase_window_days": %s,', json_value(phase_window_days)),
  sprintf('    "recent_table_days": %s,', json_value(recent_table_days)),
  sprintf('    "generated_at_utc": %s', json_value(format(as.POSIXct(Sys.time(), tz = "UTC"), "%Y-%m-%dT%H:%M:%SZ"))),
  "  },",
  '  "rows": ['
)

row_json <- character(0)
if (exists("recent_table") && nrow(recent_table) > 0) {
  for (i in seq_len(nrow(recent_table))) {
    row <- recent_table[i, ]
    row_json <- c(row_json, paste0(
      "    {",
      sprintf('"date_utc": %s, ', json_value(row$date)),
      sprintf('"median_magnitude": %s, ', json_value(row$median_magnitude)),
      sprintf('"observation_count": %s, ', json_value(as.numeric(row$observation_count), digits = 0)),
      sprintf('"cycle_reference_magnitude": %s, ', json_value(row$cycle_reference_magnitude)),
      sprintf('"cycle_reference_count": %s, ', json_value(as.numeric(row$cycle_reference_count), digits = 0)),
      sprintf('"delta_from_reference": %s', json_value(row$delta_from_reference)),
      "}"
    ))
  }
}

if (length(row_json) > 1) {
  row_json[-length(row_json)] <- paste0(row_json[-length(row_json)], ",")
}

json_lines <- c(metadata, row_json, "  ]", "}")
writeLines(json_lines, table_output_path, useBytes = TRUE)

message(sprintf("Wrote %s", plot_output_path))
message(sprintf("Wrote %s", table_output_path))
