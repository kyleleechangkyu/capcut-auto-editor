import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.util.LinkedList;

import kr.kaist.ir.korean.data.TaggedMorpheme;
import kr.kaist.ir.korean.data.TaggedSentence;
import kr.kaist.ir.korean.data.TaggedWord;
import kr.kaist.ir.korean.tagger.IntegratedTagger;
import kr.kaist.ir.korean.tagger.IntegratedTagger.ParseStructure;
import kr.kaist.ir.korean.tagger.Tagger;

/**
 * stdin으로 한 줄(문단)씩 받아 형태소 분석한 뒤, 각 어절의 마지막 형태소
 * 태그를 함께 출력합니다. 자막 컷 지점(문장 종결/화제 조사) 판단용.
 *
 * 입력: 한 줄 = 분석할 텍스트 한 덩어리 (여러 문장 가능). 빈 줄 = 구분자 무시하고 그대로 진행.
 * 출력: 각 어절마다 한 줄 -- "원문\t마지막형태소\tTAG\tRAWTAG"
 *       문장(TaggedSentence) 경계마다 "###SENT###"
 *       입력 한 줄 처리가 끝나면 "###END###"
 */
public class BreakAnalyzer {
    public static void main(String[] args) throws Exception {
        Tagger tagger = new IntegratedTagger(ParseStructure.KKMA);
        BufferedReader in = new BufferedReader(new InputStreamReader(System.in, StandardCharsets.UTF_8));
        PrintStream out = new PrintStream(System.out, true, "UTF-8");

        String line;
        while ((line = in.readLine()) != null) {
            if (line.trim().isEmpty()) {
                out.println("###END###");
                continue;
            }
            try {
                LinkedList<TaggedSentence> sents = tagger.analyzeParagraph(line);
                for (TaggedSentence s : sents) {
                    for (TaggedWord w : s) {
                        TaggedMorpheme last = w.getLast();
                        String morph = last == null ? "" : last.getMorpheme();
                        String tag = last == null ? "" : last.getTag();
                        String rawTag = last == null ? "" : last.getRawTag();
                        out.println(w.getOriginalWord() + "\t" + morph + "\t" + tag + "\t" + rawTag);
                    }
                    out.println("###SENT###");
                }
            } catch (Exception e) {
                out.println("###ERR###\t" + e.getMessage());
            }
            out.println("###END###");
        }
    }
}
