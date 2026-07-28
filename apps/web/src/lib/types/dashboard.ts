export type DashboardActivityPoint = {
  date: string;
  label: string;
  calls: number;
};

export type DashboardMetrics = {
  timezone: string;
  calls_today: number;
  calls_last_7_days: number;
  calls_previous_7_days: number;
  calls_change_from_previous_7_days: number;
  follow_up_flagged_last_7_days: number;
  average_duration_seconds_last_7_days: number | null;
  daily_activity: DashboardActivityPoint[];
};
