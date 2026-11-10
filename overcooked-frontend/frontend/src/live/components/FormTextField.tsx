import { TextField, type TextFieldProps } from '@mui/material'
import { Controller, type Control, type FieldPath, type FieldValues } from 'react-hook-form'

type Props<T extends FieldValues> = Omit<TextFieldProps, 'name' | 'value' | 'onChange' | 'onBlur'> & {
  name: FieldPath<T>
  control: Control<T>
}

/**
 * 受控的文字欄位：有既有值（編輯、返回草稿）時標籤會正確縮到上方，
 * 不會與內容重疊。錯誤訊息優先於原本的說明文字；error 可另外標示後端回報的問題（例如預算過低）。
 */
export default function FormTextField<T extends FieldValues>({ name, control, helperText, error, ...props }: Props<T>) {
  return (
    <Controller name={name} control={control} render={({ field, fieldState }) => (
      <TextField {...props} {...field} value={field.value ?? ''}
        error={!!fieldState.error || !!error} helperText={fieldState.error?.message ?? helperText} />
    )} />
  )
}
