"use client";
import Autocomplete from "@mui/material/Autocomplete";
import TextField from "@mui/material/TextField";

interface User {
  id: number;
  nickname?: string;
  email: string;
  image_url?: string;
}

interface Props {
  users: User[];
  selectedIds: number[];
  onChange: (ids: number[]) => void;
  currentUserId?: number;
}

export default function UserShareSelect({
  users,
  selectedIds,
  onChange,
  currentUserId,
}: Props) {
  const options = users.filter((u) => u.id !== currentUserId);
  const value = options.filter((u) => selectedIds.includes(u.id));
  return (
    <Autocomplete
      multiple
      options={options}
      value={value}
      onChange={(e, newValue) => onChange(newValue.map((u) => u.id))}
      getOptionLabel={(option) => option.nickname || option.email}
      renderOption={(props, option) => (
        <li {...props} className="flex items-center gap-2">
          {option.image_url && (
            <img
              src={option.image_url}
              alt={option.nickname || option.email}
              className="w-6 h-6 rounded-full"
            />
          )}
          <span>{option.nickname || option.email}</span>
        </li>
      )}
      renderInput={(params) => (
        <TextField
          {...params}
          label="Share with users"
          placeholder="Select users"
        />
      )}
    />
  );
}
