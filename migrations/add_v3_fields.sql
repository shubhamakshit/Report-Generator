-- Add new columns to the 'questions' table
ALTER TABLE questions ADD COLUMN topic TEXT;
ALTER TABLE questions ADD COLUMN time_taken INTEGER;
ALTER TABLE questions ADD COLUMN difficulty TEXT;
ALTER TABLE questions ADD COLUMN source TEXT DEFAULT 'manual';
ALTER TABLE questions ADD COLUMN test_id TEXT;
ALTER TABLE questions ADD COLUMN test_mapping_id TEXT;

-- Add new columns to the 'sessions' table
ALTER TABLE sessions ADD COLUMN test_id TEXT;
ALTER TABLE sessions ADD COLUMN test_mapping_id TEXT;
ALTER TABLE sessions ADD COLUMN source TEXT DEFAULT 'manual';
ALTER TABLE sessions ADD COLUMN metadata TEXT;

-- Create indexes for performance
CREATE INDEX IF NOT EXISTS idx_questions_test_mapping_id ON questions (test_mapping_id);
CREATE INDEX IF NOT EXISTS idx_sessions_test_mapping_id ON sessions (test_mapping_id);
