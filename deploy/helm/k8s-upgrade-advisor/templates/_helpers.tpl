{{- define "advisor.name" -}}
{{- .Chart.Name -}}
{{- end -}}

{{- define "advisor.fullname" -}}
{{- printf "%s" .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "advisor.labels" -}}
app.kubernetes.io/name: {{ include "advisor.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "advisor.selectorLabels" -}}
app.kubernetes.io/name: {{ include "advisor.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
