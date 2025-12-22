{{/*
Expand the name of the chart.
*/}}
{{- define "acm-switchover.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "acm-switchover.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "acm-switchover.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "acm-switchover.labels" -}}
helm.sh/chart: {{ include "acm-switchover.chart" . }}
{{ include "acm-switchover.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "acm-switchover.selectorLabels" -}}
app.kubernetes.io/name: {{ include "acm-switchover.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "acm-switchover.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "acm-switchover.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Build the command arguments for acm-switchover
*/}}
{{- define "acm-switchover.args" -}}
{{- if .Values.decommission.enabled }}
- "--decommission"
- "--primary-context"
- {{ required "switchover.primaryContext is required" .Values.switchover.primaryContext | quote }}
{{- if .Values.decommission.nonInteractive }}
- "--non-interactive"
{{- end }}
{{- else }}
- "--primary-context"
- {{ required "switchover.primaryContext is required" .Values.switchover.primaryContext | quote }}
- "--secondary-context"
- {{ required "switchover.secondaryContext is required" .Values.switchover.secondaryContext | quote }}
- "--method"
- {{ .Values.switchover.method | quote }}
- "--old-hub-action"
- {{ .Values.switchover.oldHubAction | quote }}
{{- end }}
{{- if .Values.switchover.dryRun }}
- "--dry-run"
{{- end }}
{{- if .Values.switchover.skipPreflight }}
- "--skip-preflight"
{{- end }}
{{- if .Values.switchover.verbose }}
- "--verbose"
{{- end }}
{{- if .Values.switchover.logFormat }}
- "--log-format"
- {{ .Values.switchover.logFormat | quote }}
{{- end }}
{{- range .Values.switchover.extraArgs }}
- {{ . | quote }}
{{- end }}
{{- end }}
