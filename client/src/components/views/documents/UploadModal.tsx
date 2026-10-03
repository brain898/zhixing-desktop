import React, { useState, useRef } from 'react';
import { X, UploadCloud, AlertCircle, CheckCircle2, RotateCw, FileText } from 'lucide-react';
import { api } from '../../../services/api';
import { UploadResult } from '../../../types';
import { formatVersionLabel } from '../../../utils/formatters';

interface UploadModalProps {
  isOpen: boolean;
  onClose: () => void;
  onUploadSuccess: () => void;
}

interface FileItemState {
  file: File;
  status: 'pending' | 'uploading' | 'success' | 'failed' | 'conflict';
  message?: string;
  result?: UploadResult;
}

export const UploadModal: React.FC<UploadModalProps> = ({ isOpen, onClose, onUploadSuccess }) => {
  const [fileList, setFileList] = useState<FileItemState[]>([]);
  const [uploading, setUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  if (!isOpen) return null;

  const handleFilesChosen = (e: React.ChangeEvent<HTMLInputElement>) => {
    if (!e.target.files) return;
    const chosen = Array.from(e.target.files);

    if (fileList.length + chosen.length > 10) {
      alert('单批上传数量不能超过 10 个文件');
      return;
    }

    const newItems: FileItemState[] = [];
    for (const f of chosen) {
      const ext = f.name.substring(f.name.lastIndexOf('.')).toLowerCase();
      if (!['.pdf', '.docx', '.txt', '.md', '.markdown'].includes(ext)) {
        newItems.push({
          file: f,
          status: 'failed',
          message: `格式「${ext}」不受支持，仅支持 PDF、DOCX、TXT、MD`,
        });
        continue;
      }
      if (f.size > 20 * 1024 * 1024) {
        newItems.push({
          file: f,
          status: 'failed',
          message: '单文件大小超过 20MB 上限',
        });
        continue;
      }
      if (f.size === 0) {
        newItems.push({
          file: f,
          status: 'failed',
          message: '文件内容为空',
        });
        continue;
      }

      newItems.push({
        file: f,
        status: 'pending',
      });
    }

    setFileList((prev) => [...prev, ...newItems]);
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  };

  const uploadSingle = async (
    itemIndex: number,
    duplicateMode = 'ask',
    targetDocId?: string
  ) => {
    const item = fileList[itemIndex];
    if (!item) return;

    setFileList((prev) => {
      const copy = [...prev];
      copy[itemIndex] = { ...item, status: 'uploading', message: undefined };
      return copy;
    });

    try {
      const res = await api.uploadDocument(item.file, duplicateMode, targetDocId);
      if (res.status === 'conflict_name') {
        setFileList((prev) => {
          const copy = [...prev];
          copy[itemIndex] = {
            ...item,
            status: 'conflict',
            message: '存在同名资料但内容不同',
            result: res,
          };
          return copy;
        });
      } else if (res.status === 'duplicate_content') {
        setFileList((prev) => {
          const copy = [...prev];
          copy[itemIndex] = {
            ...item,
            status: 'success',
            message: '企业内已有相同内容资料，已自动定位',
            result: res,
          };
          return copy;
        });
      } else {
        setFileList((prev) => {
          const copy = [...prev];
          copy[itemIndex] = {
            ...item,
            status: 'success',
            message: `已保存并创建任务（${formatVersionLabel(res.version_label)}）`,
            result: res,
          };
          return copy;
        });
      }
    } catch (err: any) {
      setFileList((prev) => {
        const copy = [...prev];
        copy[itemIndex] = {
          ...item,
          status: 'failed',
          message: err.message || '上传保存失败',
        };
        return copy;
      });
    }
  };

  const handleStartUpload = async () => {
    setUploading(true);
    for (let i = 0; i < fileList.length; i++) {
      if (fileList[i].status === 'pending') {
        await uploadSingle(i, 'ask');
      }
    }
    setUploading(false);
    onUploadSuccess();
  };

  const handleResolveConflict = async (itemIndex: number, mode: 'new_version' | 'new_document') => {
    const item = fileList[itemIndex];
    if (!item || !item.result) return;
    await uploadSingle(itemIndex, mode, item.result.existing_document_id);
    onUploadSuccess();
  };

  const handleRemoveItem = (index: number) => {
    setFileList((prev) => prev.filter((_, i) => i !== index));
  };

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        backgroundColor: 'rgba(18, 26, 22, 0.42)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 1000,
        userSelect: 'none',
      }}
    >
      <div
        style={{
          width: '540px',
          maxHeight: '80vh',
          backgroundColor: '#FFFFFF',
          borderRadius: 'var(--radius-lg)',
          boxShadow: 'var(--shadow-lg)',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
        }}
      >
        {/* 标题栏 */}
        <div
          style={{
            padding: '16px 20px',
            borderBottom: '1px solid var(--border-color)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
          }}
        >
          <div style={{ fontSize: 'var(--font-size-section)', fontWeight: 600, color: 'var(--text-primary)' }}>
            导入物业资料
          </div>
          <button
            onClick={onClose}
            disabled={uploading}
            style={{ color: 'var(--text-muted)', cursor: 'pointer' }}
          >
            <X size={18} />
          </button>
        </div>

        {/* 内容区 */}
        <div style={{ padding: '20px', overflowY: 'auto', flex: 1, display: 'flex', flexDirection: 'column', gap: '16px' }}>
          {/* 上传拖放提示区 */}
          <div
            onClick={() => fileInputRef.current?.click()}
            style={{
              border: '2px dashed var(--border-color)',
              borderRadius: 'var(--radius-md)',
              padding: '24px',
              textAlign: 'center',
              backgroundColor: 'var(--bg-secondary)',
              cursor: 'pointer',
              transition: 'border-color 120ms ease',
            }}
            onMouseEnter={(e) => (e.currentTarget.style.borderColor = 'var(--brand-accent)')}
            onMouseLeave={(e) => (e.currentTarget.style.borderColor = 'var(--border-color)')}
          >
            <UploadCloud size={32} color="var(--brand-accent)" style={{ margin: '0 auto 8px auto' }} />
            <div style={{ fontSize: 'var(--font-size-sm)', fontWeight: 500, color: 'var(--text-primary)' }}>
              点击选择文件
            </div>
            <div style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)', marginTop: '4px' }}>
              支持 PDF、DOCX、TXT、Markdown 格式；单文件不超过 20MB；单批不超过 10 个
            </div>
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept=".pdf,.docx,.txt,.md,.markdown"
              onChange={handleFilesChosen}
              style={{ display: 'none' }}
            />
          </div>

          {/* 选中的文件清单 */}
          {fileList.length > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
              <div style={{ fontSize: 'var(--font-size-xs)', fontWeight: 600, color: 'var(--text-secondary)' }}>
                待导入清单 ({fileList.length})
              </div>

              {fileList.map((item, idx) => (
                <div
                  key={idx}
                  style={{
                    padding: '10px 12px',
                    borderRadius: 'var(--radius-sm)',
                    border: '1px solid var(--border-color)',
                    backgroundColor: '#FFFFFF',
                    display: 'flex',
                    flexDirection: 'column',
                    gap: '6px',
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', minWidth: 0, flex: 1 }}>
                      <FileText size={15} color="var(--text-secondary)" style={{ flexShrink: 0 }} />
                      <span
                        title={item.file.name}
                        style={{
                          fontSize: 'var(--font-size-xs)',
                          fontWeight: 500,
                          color: 'var(--text-primary)',
                          whiteSpace: 'nowrap',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                        }}
                      >
                        {item.file.name}
                      </span>
                      <span style={{ fontSize: 'var(--font-size-xs)', color: 'var(--text-muted)', flexShrink: 0 }}>
                        ({(item.file.size / 1024).toFixed(0)} KB)
                      </span>
                    </div>

                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexShrink: 0 }}>
                      {item.status === 'uploading' && (
                        <span style={{ fontSize: 'var(--font-size-xs)', color: 'var(--pending-text)', display: 'flex', alignItems: 'center', gap: '4px' }}>
                          <RotateCw size={12} className="spin-slow" /> 上传中
                        </span>
                      )}
                      {item.status === 'success' && (
                        <span style={{ fontSize: 'var(--font-size-xs)', color: 'var(--success-text)', display: 'flex', alignItems: 'center', gap: '4px' }}>
                          <CheckCircle2 size={12} /> 成功
                        </span>
                      )}
                      {item.status === 'failed' && (
                        <span style={{ fontSize: 'var(--font-size-xs)', color: 'var(--error-text)', display: 'flex', alignItems: 'center', gap: '4px' }}>
                          <AlertCircle size={12} /> 失败
                        </span>
                      )}
                      {item.status === 'conflict' && (
                        <span style={{ fontSize: 'var(--font-size-xs)', color: 'var(--pending-text)', display: 'flex', alignItems: 'center', gap: '4px' }}>
                          <AlertCircle size={12} /> 同名冲突
                        </span>
                      )}

                      {!uploading && item.status !== 'uploading' && (
                        <button
                          onClick={() => handleRemoveItem(idx)}
                          style={{ color: 'var(--text-muted)', cursor: 'pointer', padding: '2px' }}
                          title="移除"
                        >
                          <X size={13} />
                        </button>
                      )}
                    </div>
                  </div>

                  {/* 详细提示信息 */}
                  {item.message && (
                    <div
                      style={{
                        fontSize: 'var(--font-size-xs)',
                        color: item.status === 'failed' ? 'var(--error-text)' : 'var(--text-secondary)',
                      }}
                    >
                      {item.message}
                    </div>
                  )}

                  {/* 同名冲突处理按钮 */}
                  {item.status === 'conflict' && (
                    <div style={{ display: 'flex', gap: '8px', marginTop: '4px' }}>
                      <button
                        type="button"
                        onClick={() => handleResolveConflict(idx, 'new_version')}
                        className="btn-secondary"
                        style={{ fontSize: 'var(--font-size-xs)', height: '24px', padding: '0 8px' }}
                      >
                        作为已有文件的新版本
                      </button>
                      <button
                        type="button"
                        onClick={() => handleResolveConflict(idx, 'new_document')}
                        className="btn-secondary"
                        style={{ fontSize: 'var(--font-size-xs)', height: '24px', padding: '0 8px' }}
                      >
                        作为独立文件保存
                      </button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* 底部按钮栏 */}
        <div
          style={{
            padding: '14px 20px',
            borderTop: '1px solid var(--border-color)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'flex-end',
            gap: '10px',
            backgroundColor: '#FFFFFF',
          }}
        >
          <button
            type="button"
            className="btn-secondary"
            onClick={onClose}
            disabled={uploading}
            style={{ height: '34px', fontSize: '13px' }}
          >
            关闭
          </button>
          <button
            type="button"
            className="btn-primary"
            onClick={handleStartUpload}
            disabled={uploading || fileList.filter((f) => f.status === 'pending').length === 0}
            style={{ height: '34px', fontSize: '13px' }}
          >
            {uploading ? '正在批量处理...' : '开始导入'}
          </button>
        </div>
      </div>
    </div>
  );
};
