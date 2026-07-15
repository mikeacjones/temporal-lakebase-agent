{{- define "loan-agent.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "loan-agent.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name (include "loan-agent.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "loan-agent.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | quote }}
app.kubernetes.io/name: {{ include "loan-agent.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: temporal-lakebase-agentworkflow
{{- end -}}

{{- define "loan-agent.selectorLabels" -}}
app.kubernetes.io/name: {{ include "loan-agent.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "loan-agent.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{- default (include "loan-agent.fullname" .) .Values.serviceAccount.name -}}
{{- else -}}
{{- required "serviceAccount.name is required when serviceAccount.create=false" .Values.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{- define "loan-agent.temporalConnectionName" -}}
{{- default (printf "%s-temporal" (include "loan-agent.fullname" .)) .Values.temporal.connectionName | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "loan-agent.workerName" -}}
{{- printf "%s-worker" (include "loan-agent.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "loan-agent.databaseSecretEnv" -}}
{{- if eq .Values.lakebase.connectionMode "endpoint" }}
- name: DATABRICKS_AUTH_TYPE
  value: oauth-m2m
- name: PGSSLMODE
  value: require
- name: DATABRICKS_CLIENT_ID
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.existingName }}
      key: {{ .Values.secrets.keys.databricksClientId }}
- name: LAKEBASE_USER
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.existingName }}
      key: {{ .Values.secrets.keys.databricksClientId }}
- name: DATABRICKS_CLIENT_SECRET
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.existingName }}
      key: {{ .Values.secrets.keys.databricksClientSecret }}
{{- else if eq .Values.lakebase.connectionMode "url" }}
- name: LAKEBASE_PG_URL
  valueFrom:
    secretKeyRef:
      name: {{ .Values.secrets.existingName }}
      key: {{ .Values.secrets.keys.lakebasePgUrl }}
{{- end }}
{{- end -}}
