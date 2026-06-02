{{/* vim: set filetype=mustache: */}}
{{/*
Expand the name of the chart.
*/}}
{{- define "acm-switchover-rbac.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "acm-switchover-rbac.fullname" -}}
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
{{- define "acm-switchover-rbac.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "acm-switchover-rbac.labels" -}}
helm.sh/chart: {{ include "acm-switchover-rbac.chart" . }}
{{ include "acm-switchover-rbac.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
{{- with .Values.commonLabels }}
{{ toYaml . }}
{{- end }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "acm-switchover-rbac.selectorLabels" -}}
app.kubernetes.io/name: {{ include "acm-switchover-rbac.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Validate that custom validator ClusterRole rules remain read-only.
*/}}
{{- define "acm-switchover-rbac.validateValidatorCustomRules" -}}
{{- $allowedVerbs := dict "get" true "list" true "watch" true -}}
{{- range $ruleIndex, $rule := .Values.rbac.customValidatorRules }}
{{- if not (kindIs "map" $rule) }}
{{- fail (printf "rbac.customValidatorRules entries must be mappings; rule %d is invalid" $ruleIndex) }}
{{- end }}
{{- if not (kindIs "slice" $rule.verbs) }}
{{- fail (printf "rbac.customValidatorRules verbs must be a list of strings; rule %d is missing or invalid" $ruleIndex) }}
{{- end }}
{{- range $verb := $rule.verbs }}
{{- $verbText := toString $verb -}}
{{- if not (hasKey $allowedVerbs $verbText) }}
{{- fail (printf "rbac.customValidatorRules may only use read-only verbs [get, list, watch]; rule %d includes disallowed verb %q" $ruleIndex $verbText) }}
{{- end }}
{{- end }}
{{- end }}
{{- end }}
