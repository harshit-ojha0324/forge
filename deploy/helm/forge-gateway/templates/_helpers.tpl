{{- define "forge-gateway.labels" -}}
app.kubernetes.io/name: forge-gateway
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/part-of: forge
{{- end }}

{{- define "forge-gateway.selectorLabels" -}}
app.kubernetes.io/name: forge-gateway
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}
