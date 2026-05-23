# SKKU InSight — Third-Party Notices

본 저장소는 다음 third-party 데이터·라이브러리를 포함하며, 각 라이선스 조건에 따라 attribution / 재배포 의무를 이행한다.

## Data

### Computer Science Ontology (CSO) 3.5

- **위치**: [`data/cso/CSO.3.5.csv`](data/cso/CSO.3.5.csv) (25.7 MB, csv-quoted N-Triples)
- **저자**: Knowledge Media Institute (KMI), The Open University
- **출처**: <https://cso.kmi.open.ac.uk/downloads>
- **라이선스**: [Creative Commons Attribution 4.0 International (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/)
- **인용 논문**: Salatino, A.A., Thanapalasingam, T., Mannocci, A., Osborne, F. and Motta, E. (2018). *The Computer Science Ontology: A Large-Scale Taxonomy of Research Areas*. In Proceedings of the International Semantic Web Conference (ISWC 2018).
- **본 저장소 활용**: 사용자 관심 토픽 그래프 (`backend/app/topic/`) 의 base ontology. 14k+ 노드 × 44k+ 엣지 BFS · 후손 BFS · cluster 분류 (`broad_interests.toml` 12 cluster). raw 파일 → `backend/scripts/import_cso.py` → `cso_topic` 테이블 영속.
- **재배포 근거**: CC BY 4.0 는 attribution 명시 시 자유로운 재배포·수정·상업적 이용을 허용. 본 NOTICE 와 [`docs/data/cso-import.md`](docs/data/cso-import.md) 에서 attribution 이행.
