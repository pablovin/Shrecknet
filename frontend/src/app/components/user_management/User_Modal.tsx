"use client";
import { useState } from "react";
import { FaTrash, FaUnlock } from "react-icons/fa";
import { ROLES } from "../../lib/roles";
import { uploadImage } from "../../lib/uploadImage";
import { updateUser, deleteUser } from "../../lib/usersApi";
import { useAuth } from "../auth/AuthProvider";
import ModalContainer from "../template/modalContainer";
import { M3FloatingInput } from "../template/M3FloatingInput";
import Image from "next/image";
async function uploadAvatar(file, userId) {
  const customFileName = userId.toString();
  const imageUrl = await uploadImage(
    file,
    "avatars",
    userId.toString(),
    customFileName
  );
  // Append a cache buster so the browser doesn't show a cached avatar
  return `${imageUrl}?v=${Date.now()}`;
}

export default function UserModal({
  user,
  onClose,
  onSave,
  onDelete,
  isProfile = false,
  setError,
}) {
  const [form, setForm] = useState({
    nickname: user.nickname,
    email: user.email,
    role: user.role,
    image_url: user.image_url,
    newAvatar: null,
  });
  const [password, setPassword] = useState("");
  const { token } = useAuth();
  const [error, setLocalError] = useState("");
  const [confirmUnlock, setConfirmUnlock] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [saving, setSaving] = useState(false);

  

  async function handleAvatarChange(e) {
    const file = e.target.files?.[0];
    if (file) {
      try {
        const avatarUrl = await uploadAvatar(file, user.id);
        setForm(f => ({ ...f, image_url: avatarUrl, newAvatar: file }));
      } catch (err) {
        setLocalError("Avatar upload failed. " + err);
        setError?.("Avatar upload failed. " + err);
      }
    }
  }

  async function handleSave(e) {
    e?.preventDefault?.();
    setLocalError("");
    setError?.("");
    setSaving(true);
    try {
      const updatePayload = {
        nickname: form.nickname,
        role: form.role,
        image_url: form.image_url,
      };
      if (isProfile && password) updatePayload.password = password;
      await updateUser(user.id, updatePayload, token);
      onSave?.();
      onClose();
    } catch (err) {
      const msg = "Update failed. " + (err?.detail || err?.message || err);
      setLocalError(msg);
      setError?.(msg);
    }
    setSaving(false);
  }

  async function handleDelete() {
    setLocalError("");
    setError?.("");
    try {
      await deleteUser(user.id, token);
      onDelete?.();
      onClose();
    } catch (err) {
      const msg = "Delete failed. " + (err?.detail || err?.message || err);
      setLocalError(msg);
      setError?.(msg);
    }
  }

  return (
    <ModalContainer title={isProfile ? "Edit Your Profile" : "Edit User"} onClose={onClose}>
      {(error) && (
        <div className="bg-red-100 text-red-700 rounded-lg px-3 py-2 mb-3 text-sm">
          {error}
        </div>
      )}

      {/* Avatar, with card effect */}
      <div className="flex flex-col items-center mb-6">
        <div className="bg-[var(--surface-variant)] rounded-2xl shadow-xl w-28 h-28 flex items-center justify-center border-2 border-[var(--primary)] mb-1">
          <Image
            src={form.image_url || "/images/avatars/default.png"}
            className="w-24 h-24 rounded-2xl object-cover"
            width={400}
            height={400}
            alt="Avatar"
          />
        </div>
        <input
          type="file"
          accept="image/*"
          className="hidden"
          id="avatar-upload"
          onChange={handleAvatarChange}
        />
        <label
          htmlFor="avatar-upload"
          className="bg-[var(--primary)]/10 hover:bg-[var(--primary)]/30 px-4 py-1 mt-2 rounded-full text-[var(--primary)] font-semibold cursor-pointer text-xs transition"
        >
          Upload Avatar
        </label>
      </div>

      <form className="flex flex-col gap-4" onSubmit={handleSave}>
        <M3FloatingInput
          label="Nickname"
          name="nickname"
          value={form.nickname}
          onChange={e => setForm(f => ({ ...f, nickname: e.target.value }))}
          required
        />
        {!isProfile && (
          <div className="relative">
            <select
              className="peer w-full px-4 pt-6 pb-2 text-[var(--foreground)] bg-[var(--surface)] border-2 border-[var(--border)] rounded-xl outline-none focus:border-[var(--primary)] transition-colors text-base"
              value={form.role}
              onChange={e => setForm(f => ({ ...f, role: e.target.value }))}
              required
            >
              {ROLES.map(role => (
                <option key={role} value={role}>{role}</option>
              ))}
            </select>
            <label
              className="absolute left-3 top-1.5 text-base text-[var(--primary)] font-semibold pointer-events-none"
              style={{ zIndex: 10 }}
            >
              Role
            </label>
          </div>
        )}
        {isProfile && (
          <M3FloatingInput
            label="New Password"
            name="password"
            type="password"
            value={password}
            autoComplete="new-password"
            onChange={e => setPassword(e.target.value)}
            minLength={8}
            maxLength={100}
            placeholder=" "
          />
        )}

        <div className="flex flex-row-reverse gap-3 mt-3">
          <button
            type="submit"
            className="px-7 py-2 rounded-xl font-bold bg-[var(--primary)] text-[var(--primary-foreground)] shadow-md hover:bg-[var(--accent)] hover:text-[var(--primary)] border border-[var(--primary)]/30 transition-all"
            disabled={saving}
          >
            {saving ? "Saving..." : "Save"}
          </button>
          <button
            type="button"
            onClick={onClose}
            disabled={saving}
            className="px-6 py-2 rounded-xl font-semibold bg-transparent border border-[var(--border)] text-[var(--primary)] hover:bg-[var(--surface-variant)] transition-all"
          >
            Cancel
          </button>
        </div>
      </form>

      {!isProfile && (
        <div className="mt-8 flex flex-col items-center">
          {!confirmUnlock ? (
            <button
              className="bg-[var(--surface)]/60 hover:bg-[var(--primary)]/10 px-4 py-2 rounded-full flex items-center gap-2 mb-2 border border-[var(--primary)]/30 text-[var(--primary)]"
              onClick={() => setConfirmUnlock(true)}
              title="Unlock delete"
            >
              <FaUnlock /> <span>Unlock Delete</span>
            </button>
          ) : !confirmDelete ? (
            <button
              className="bg-red-600 hover:bg-red-800 px-4 py-2 rounded-full flex items-center text-white gap-2 mb-2"
              onClick={() => setConfirmDelete(true)}
              title="Delete"
            >
              <FaTrash /> <span>Delete</span>
            </button>
          ) : (
            <div className="flex flex-col items-center">
              <div className="mb-2 font-semibold text-red-700 text-center">
                Are you sure you want to delete the user <span className="font-bold">{user.nickname}</span>?
              </div>
              <div className="flex gap-3">
                <button
                  className="px-5 py-2 bg-red-600 text-white rounded-lg hover:bg-red-800 font-bold"
                  onClick={handleDelete}
                >
                  Confirm
                </button>
                <button
                  className="px-5 py-2 bg-[var(--primary)]/10 text-[var(--primary)] rounded-lg hover:bg-[var(--primary)]/20 font-bold"
                  onClick={() => { setConfirmDelete(false); setConfirmUnlock(false); }}
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </ModalContainer>
  );
}
